"""Runs verify commands (upstream code) inside an OS-enforced sandbox.

The command sees a fresh HOME and TMPDIR inside the work directory and a
scrubbed environment. It may read only the stage, those two folders and the
toolchain (system folders, the interpreter's prefix, PATH folders), and write
only the stage and those two folders. IP networking stays open (verify commands
install pinned wheels with pip); Unix sockets are limited to the writable folders,
plus the system DNS resolver on macOS, so no host daemon (Docker, ssh-agent, a
keyring) is reachable.

Backends, in order: macOS `sandbox-exec` with a generated profile; Linux
bubblewrap (`bwrap`); Linux docker or podman with the host toolchain mounted
read-only. `SKILL_SYNC_SANDBOX` picks one explicitly (`none` disables them).
With no backend, verify refuses to run unless the caller passed
--unsandboxed-verify, which is meant for disposable CI runners only.

Every command runs in its own session. On timeout, cancellation or normal exit,
skill-sync kills its process group and every descendant (from a process-tree
snapshot taken before anything dies), plus orphans of this run: on Linux those
carrying the run's environment token, on macOS those under the run's sandbox
profile (see _SandboxProbe). It sweeps until none is left. bwrap and the
containers also run the command in a PID namespace that dies with it.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import SkillSyncError

BACKENDS = ("sandbox-exec", "bwrap", "docker", "podman")
UNSANDBOXED = "unsandboxed"
# Linux container backends mount the host toolchain over this image's root, so its
# contents barely matter; it is pinned so the root filesystem can't change under us.
# busybox:1.37.0, multi-arch index pinned by digest.
CONTAINER_IMAGE = "docker.io/library/busybox@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec"
PASSTHROUGH_ENV = ("PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "DEVELOPER_DIR")
KILL_GRACE_SECONDS = 5


class SandboxUnavailable(SkillSyncError):
    def __init__(self, message: str):
        super().__init__(message, 4)


def _real(path) -> str:
    return os.path.realpath(str(path))


def _bwrap_works(binary: str) -> bool:
    try:
        return subprocess.run([binary, "--ro-bind", "/", "/", "--unshare-all", "true"],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def absolute_path_entries(path: Optional[str] = None) -> str:
    """PATH without relative entries ("", ".", "bin"): a planted file in the cwd can't shadow a tool."""
    entries = (os.environ.get("PATH", "") if path is None else path).split(os.pathsep)
    return os.pathsep.join(e for e in entries if e and os.path.isabs(e))


def which(name: str) -> Optional[str]:
    """An executable's absolute path, looked up on the absolute PATH entries only."""
    found = shutil.which(name, path=absolute_path_entries())
    return os.path.realpath(found) if found else None


# Fixed locations first, so nothing on PATH can stand in for the sandbox itself.
FIXED = {"sandbox-exec": "/usr/bin/sandbox-exec", "bwrap": "/usr/bin/bwrap"}


def backend_executable(backend: str) -> str:
    fixed = FIXED.get(backend)
    if fixed and os.access(fixed, os.X_OK):
        return fixed
    found = which(backend)
    if not found:
        raise SandboxUnavailable(f"{backend} is not installed")
    return found


def choose_backend(allow_unsandboxed: bool) -> str:
    requested = os.environ.get("SKILL_SYNC_SANDBOX", "auto")
    if requested not in ("auto", "none") + BACKENDS:
        raise SkillSyncError(f"SKILL_SYNC_SANDBOX={requested!r}: use auto, none or one of {', '.join(BACKENDS)}", 2)
    found: Optional[str] = None
    if requested in BACKENDS:
        if requested == "sandbox-exec" and sys.platform != "darwin":
            raise SandboxUnavailable("sandbox-exec is macOS-only")
        if requested in ("bwrap", "docker", "podman") and not sys.platform.startswith("linux"):
            raise SandboxUnavailable(f"the {requested} backend runs the host toolchain, so it is Linux-only")
        if not which(requested) and not os.access(FIXED.get(requested, "/nonexistent"), os.X_OK):
            raise SandboxUnavailable(f"SKILL_SYNC_SANDBOX={requested} but {requested} is not installed")
        found = requested
    elif requested == "auto":
        if sys.platform == "darwin" and os.access(FIXED["sandbox-exec"], os.X_OK):
            found = "sandbox-exec"
        elif sys.platform.startswith("linux"):
            bwrap = os.access(FIXED["bwrap"], os.X_OK) and FIXED["bwrap"] or which("bwrap")
            if bwrap and _bwrap_works(bwrap):
                found = "bwrap"
            else:
                found = next((b for b in ("docker", "podman") if which(b)), None)
    if found:
        return found
    if allow_unsandboxed:
        return UNSANDBOXED
    raise SandboxUnavailable(
        "no sandbox is available for verify commands, which run upstream code: install bubblewrap (bwrap), "
        "docker or podman on Linux (macOS uses sandbox-exec). On a disposable CI runner with no secrets, "
        "pass --unsandboxed-verify to run them without one.")


# ------------------------------------------------------------------- paths --

SYSTEM_READ = {
    "darwin": ["/System", "/usr", "/bin", "/sbin", "/private/etc", "/private/var/db/timezone",
               "/private/var/db/dyld", "/private/var/db/xcode_select_link", "/Library/Developer/CommandLineTools",
               "/Applications/Xcode.app", "/opt/homebrew", "/usr/local", "/dev"],
    "linux": ["/usr", "/etc", "/opt", "/nix/store"],
}


def _blocked(real: str, home: str, forbidden: List[str]) -> bool:
    """True for "/", HOME or any ancestor of it, and anything overlapping a forbidden folder."""
    if real == "/" or real == home or home.startswith(real + os.sep):
        return True
    return any(real == f or real.startswith(f + os.sep) or f.startswith(real + os.sep) for f in forbidden)


def toolchain_reads(interpreters: List[str], forbidden: List[str]) -> List[str]:
    """System folders, each interpreter's prefixes and the PATH folders; never HOME or the repository."""
    home = _real(Path.home())
    blocked = [_real(p) for p in forbidden]
    paths = list(SYSTEM_READ["darwin" if sys.platform == "darwin" else "linux"])
    for interpreter in interpreters:
        paths += _interpreter_prefixes(interpreter)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry) or not os.path.isdir(entry):
            continue
        real = _real(entry)
        paths.append(real)
        parent = os.path.dirname(real)
        # A prefix's bin/ usually needs its lib/ too. Not inside HOME, where the
        # parent (say ~/.local) can hold credentials and keyrings.
        if os.path.basename(real) == "bin" and not parent.startswith(home + os.sep):
            paths.append(parent)
    allowed: List[str] = []
    for path in paths:
        real = _real(path)
        if not _blocked(real, home, blocked) and real not in allowed:
            allowed.append(real)
    return allowed


def _interpreter_prefixes(interpreter: str) -> List[str]:
    real = _real(interpreter)
    prefixes = [os.path.dirname(os.path.dirname(real))]
    try:
        out = subprocess.run([real, "-I", "-c", "import sys; print(sys.base_prefix); print(sys.prefix)"],
                             capture_output=True, text=True, timeout=20).stdout.split("\n")
        prefixes += [p for p in out if p.strip()]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return prefixes


# ----------------------------------------------------------------- backends --

def _ancestors(path: str) -> List[str]:
    parts, current = [], os.path.dirname(path)
    while current not in ("/", ""):
        parts.append(current)
        current = os.path.dirname(current)
    return parts


def _sb_regex_prefix(prefix: str) -> str:
    # Prefixes are validated to [A-Za-z0-9._-]; only "." is special in a regex.
    return prefix.replace(".", "[.]")


def _sb_quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_profile(read: List[str], write: List[str], hidden: List[str], shared_tmp: List[str] = (),
                  marker: Optional[str] = None) -> str:
    reads = " ".join(f"(subpath {_sb_quote(p)})" for p in read + write)
    writes = " ".join(f"(subpath {_sb_quote(p)})" for p in write)
    # Tools that open each path component from "/" (openat, O_NOFOLLOW) need to open the
    # writable folders' ancestors. Exact directories only, so just their entry names show;
    # never HOME, the repository or the cache.
    ancestors = sorted({a for p in write for a in _ancestors(p)
                        if not any(a == h or a.startswith(h + os.sep) for h in hidden)})
    return "\n".join([
        "(version 1)",
        "(deny default)",
        "(allow process-fork)",
        "(allow process-exec)",
        "(allow signal (target same-sandbox))",
        "(allow sysctl-read)",
        # IP only: outbound anywhere (pip), and listening/connecting on local addresses
        # (test servers). Unix sockets only inside the writable folders, plus the DNS
        # resolver's socket (getaddrinfo fails without it); no other host daemon.
        "(allow system-socket)",
        "(allow network-outbound (remote ip))",
        "(allow network-bind network-inbound (local ip))",
        "(allow network-outbound (literal \"/private/var/run/mDNSResponder\"))",
        f"(allow network-bind network-inbound (local unix-socket {writes}))",
        f"(allow network-outbound (remote unix-socket {writes}))",
        "(allow pseudo-tty)",
        "(allow ipc-posix-shm* ipc-posix-sem)",
        # stat() anywhere, so path resolution works; contents stay unreadable.
        "(allow file-read-metadata)",
        "(allow file-read-data (literal \"/\"))",
        *([f"(allow file-read-data {' '.join(f'(literal {_sb_quote(a)})' for a in ancestors)})"] if ancestors else []),
        f"(allow file-read* {reads})",
        # Identifies this run's processes for cleanup (see _SandboxProbe).
        *([f"(allow file-read-data (literal {_sb_quote(marker)}))"] if marker else []),
        f"(allow file-write* {writes})",
        # Declared name prefixes directly under the shared temp folders (some upstream
        # tests check that a tool refuses them); nothing else there is reachable.
        *[f"(allow file-read* file-write* (regex #\"^/private/(var/)?tmp/{_sb_regex_prefix(prefix)}[^/]*(/.*)?$\"))"
          for prefix in shared_tmp],
        "(allow file-read* file-write* file-ioctl (literal \"/dev/null\") (literal \"/dev/zero\") (literal \"/dev/tty\")"
        " (literal \"/dev/dtracehelper\") (regex #\"^/dev/fd/\") (regex #\"^/dev/ttys\"))",
        "(allow file-read* (literal \"/dev/random\") (literal \"/dev/urandom\"))",
        # Only the services ordinary toolchains need: user/group lookup, DNS,
        # notifications, logging, proxy settings, and trustd for TLS certificate
        # evaluation (pip verifies through the system trust store). Not securityd:
        # no keychain items.
        "(allow mach-lookup (global-name \"com.apple.system.opendirectoryd.libinfo\")"
        " (global-name \"com.apple.system.notification_center\") (global-name \"com.apple.logd\")"
        " (global-name \"com.apple.system.logger\") (global-name \"com.apple.dnssd.service\")"
        " (global-name \"com.apple.SystemConfiguration.configd\") (global-name \"com.apple.system.DirectoryService.libinfo_v1\")"
        " (global-name \"com.apple.diagnosticd\") (global-name \"com.apple.trustd\")"
        " (global-name \"com.apple.trustd.agent\"))",
        "",
    ])


@dataclass
class Box:
    backend: str
    cwd: str
    read: List[str]
    write: List[str]
    env: Dict[str, str]
    container: Optional[str] = None
    profile: Optional[str] = field(default=None, repr=False)
    executable: Optional[str] = None  # the backend's absolute path, resolved before any upstream code runs
    token: str = ""
    markers: Optional[Tuple[str, str]] = None  # (readable, unreadable) files that identify this run's processes

    def command(self, argv: List[str]) -> List[str]:
        if self.backend == UNSANDBOXED:
            return argv
        if self.backend == "sandbox-exec":
            return [self.executable or FIXED["sandbox-exec"], "-p", self.profile or "", *argv]
        if self.backend == "bwrap":
            return bwrap_command(self, argv)
        return container_command(self, argv)


def bwrap_command(box: Box, argv: List[str]) -> List[str]:
    cmd = [box.executable or "bwrap", "--die-with-parent", "--unshare-all", "--unshare-pid", "--share-net", "--clearenv",
           "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/var/tmp"]
    for top in ("/bin", "/sbin", "/lib", "/lib32", "/lib64"):
        if os.path.islink(top):
            cmd += ["--symlink", os.readlink(top), top]
        elif os.path.isdir(top):
            cmd += ["--ro-bind", top, top]
    for path in box.read:
        cmd += ["--ro-bind-try", path, path]
    for path in box.write:
        cmd += ["--bind", path, path]
    for key, value in sorted(box.env.items()):
        cmd += ["--setenv", key, value]
    return cmd + ["--chdir", box.cwd, "--", *argv]


def container_command(box: Box, argv: List[str]) -> List[str]:
    cmd = [box.executable or box.backend, "run", "--rm", "--init", "--name", box.container or "skill-sync-verify",
           "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges", "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/var/tmp",
           "--workdir", box.cwd]
    for top in ("/bin", "/sbin", "/lib", "/lib32", "/lib64"):
        if os.path.isdir(top) and not os.path.islink(top):
            cmd += ["--volume", f"{top}:{top}:ro"]
    for path in box.read:
        if os.path.exists(path):
            cmd += ["--volume", f"{path}:{path}:ro"]
    for path in box.write:
        cmd += ["--volume", f"{path}:{path}:rw"]
    for key, value in sorted(box.env.items()):
        cmd += ["--env", f"{key}={value}"]
    return cmd + [CONTAINER_IMAGE, *argv]



def prepare(backend: str, work: Path, stage: Path, interpreters: List[str], forbidden: List[str],
            extra_env: Dict[str, str], shared_tmp: List[str] = ()) -> Box:
    home, tmp = work / "verify-home", work / "verify-tmp"
    for path in (home, tmp):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    write = [_real(stage), _real(home), _real(tmp)]
    read = toolchain_reads(interpreters, forbidden + [str(work.parent)]) if backend != UNSANDBOXED else []
    env = {k: v for k, v in os.environ.items() if k in PASSTHROUGH_ENV}
    env["PATH"] = absolute_path_entries()
    token = secrets.token_hex(16)
    env.update(HOME=_real(home), TMPDIR=_real(tmp), TMP=_real(tmp), TEMP=_real(tmp), PYTHONNOUSERSITE="1",
               SKILL_SYNC_VERIFY_TOKEN=token)
    env.update(extra_env)
    box = Box(backend, _real(stage), read, write, env,
              container=f"skill-sync-verify-{secrets.token_hex(6)}",
              executable=None if backend == UNSANDBOXED else backend_executable(backend), token=token)
    if backend == "sandbox-exec":
        folder = work / f"markers-{secrets.token_hex(8)}"
        folder.mkdir(mode=0o700)
        allowed, denied = (_real(folder / secrets.token_hex(12)) for _ in range(2))
        for marker in (allowed, denied):
            Path(marker).write_text("")
        box.markers = (allowed, denied)
        box.profile = macos_profile(read, write, [_real(Path.home())] + [_real(p) for p in forbidden], list(shared_tmp),
                                    marker=allowed)
    return box


# ----------------------------------------------------------------- running --

def _etime_seconds(text: str) -> Optional[int]:
    """ps's elapsed time, [[dd-]hh:]mm:ss, in seconds."""
    days, _, rest = text.strip().rpartition("-")
    parts = rest.split(":")
    if not all(p.isdigit() for p in parts) or not 2 <= len(parts) <= 3 or (days and not days.isdigit()):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds + int(days or 0) * 86400


def _processes() -> List[Tuple[int, int, Optional[float], str]]:
    """(pid, ppid, age in seconds, environment) for this user's processes; environment only on Linux."""
    uid = os.getuid()
    if sys.platform.startswith("linux"):
        found = []
        ticks = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime") as handle:
            uptime = float(handle.read().split()[0])
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                if os.stat(f"/proc/{entry}").st_uid != uid:
                    continue
                with open(f"/proc/{entry}/stat", "rb") as handle:
                    fields = handle.read().rsplit(b")", 1)[1].split()
                with open(f"/proc/{entry}/environ", "rb") as handle:
                    environ = handle.read().decode("utf-8", "replace")
            except (OSError, ValueError, IndexError):
                continue
            found.append((int(entry), int(fields[1]), uptime - int(fields[19]) / ticks, environ))
        return found
    # macOS hides other processes' environments (even same-user), so no environment here.
    try:
        out = subprocess.run(["/bin/ps", "-U", str(uid), "-o", "pid=,ppid=,etime="],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            found.append((int(parts[0]), int(parts[1]), _etime_seconds(parts[2]), ""))
    return found


class _SandboxProbe:
    """Recognizes processes running under one run's macOS profile with sandbox_check(3).

    Each run creates two sibling files with random names in a fresh folder of its own,
    and its profile allows reading exactly one of them. Every other process either
    reads both (unsandboxed, or a sandbox that allows the temp tree, as app sandboxes
    do) or neither, so only a process under this run's profile tells them apart.
    Detached children inherit the profile, so they match even with a scrubbed environment.
    """

    def __init__(self, box: "Box"):
        import ctypes
        self.ctypes = ctypes
        self.check = None
        self.allowed_marker, self.denied_marker = box.markers or (None, None)
        try:
            fn = ctypes.CDLL("/usr/lib/libSystem.B.dylib").sandbox_check
            fn.restype = ctypes.c_int
            fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]  # the path is variadic
            self.check = fn
        except (OSError, AttributeError):
            pass

    def _verdict(self, pid: int, path: str) -> int:
        no_report, filter_path = 0x40000000, 1
        return self.check(pid, b"file-read-data", filter_path | no_report, self.ctypes.c_char_p(path.encode()))

    def matches(self, pid: int) -> bool:
        if not (self.check and self.allowed_marker and self.denied_marker):
            return False
        # 0 = allowed, 1 = denied; anything else (an error, a vanished pid) never matches.
        return (self.check(pid, None, 0) == 1 and self._verdict(pid, self.allowed_marker) == 0
                and self._verdict(pid, self.denied_marker) == 1)


def _targets(root: int, box: "Box", probe: Optional[_SandboxProbe], max_age: float) -> set:
    """Processes to kill: descendants of `root`, plus every same-user process tagged as this run's.

    Only processes younger than the command (`max_age` seconds) qualify, and never
    skill-sync itself or any of its ancestors.
    """
    table = _processes()
    me = os.getpid()
    parents = {pid: ppid for pid, ppid, _, _ in table}
    protected, current = set(), me
    while current and current not in protected:
        protected.add(current)
        current = parents.get(current, 0)
    young = {pid for pid, _, age, _ in table if age is not None and age <= max_age}
    children: Dict[int, List[int]] = {}
    for pid, ppid, _, _ in table:
        children.setdefault(ppid, []).append(pid)
    needle = f"SKILL_SYNC_VERIFY_TOKEN={box.token}"
    found = {pid for pid, _, _, environ in table if box.token and needle in environ}
    if probe:
        found |= {pid for pid in young if pid not in protected and probe.matches(pid)}
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return (found & young) - protected


def _kill(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def terminate(proc: subprocess.Popen, box: Box, started: float) -> None:
    """Kills the command's process group, its descendants and every token carrier, then reaps it.

    Descendants are found from the process tree before anything dies (so detached
    children are still linked to their parents). Orphans (a detached child whose
    parent already exited is reparented to launchd/init) are found by the
    environment token on Linux and by their sandbox profile on macOS. Repeats until
    a sweep finds nothing, so processes forked mid-kill are caught too.
    """
    if box.backend in ("docker", "podman"):
        subprocess.run([box.executable or box.backend, "kill", box.container or ""], capture_output=True, timeout=30)
    probe = _SandboxProbe(box) if box.backend == "sandbox-exec" else None
    deadline = time.monotonic() + KILL_GRACE_SECONDS * 2
    quiet = 0
    while time.monotonic() < deadline and quiet < 3:
        # Snapshot before killing, so children are still linked to their parents.
        # +2s: ps reports whole seconds, and the launch itself takes a moment.
        targets = _targets(proc.pid, box, probe, time.monotonic() - started + 2)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        for pid in targets:
            _kill(pid, signal.SIGKILL)
        proc.poll()
        quiet = quiet + 1 if not targets and proc.returncode is not None else 0
        time.sleep(0.05)
    proc.wait()


def run(box: Box, argv: List[str], log: Path, timeout: int) -> Tuple[Optional[int], float]:
    """(exit code, or None on timeout; seconds). The command's whole process group is gone on return."""
    started = time.monotonic()
    with open(log, "wb") as handle:
        try:
            proc = subprocess.Popen(box.command(argv), cwd=box.cwd, env=box.env,
                                    stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except OSError as exc:
            handle.write(f"could not start {argv[0]}: {exc}\n".encode())
            return 127, 0.0
        code: Optional[int] = None
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            code = None
        finally:
            # Timeout, cancellation or a normal exit: nothing the command started may outlive it.
            terminate(proc, box, started)
    return code, time.monotonic() - started
