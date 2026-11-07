"""Shared plumbing: errors, process execution, git, globs, paths."""

import codecs
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

ENTER_ROOT = 'cd -- "$1" && shift && exec "$@"'
_PLACEHOLDER = re.compile(r"\{(cmd|root)\}")

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ENV = 2


class AuditError(Exception):
    """An error that ends the command with exit code 2."""

    kind = "environment"


class UsageError(AuditError):
    kind = "usage"


class EnvError(AuditError):
    kind = "environment"


class Terminated(BaseException):
    """Raised from the SIGTERM/SIGHUP handler so `finally` blocks run."""

    def __init__(self, signum=signal.SIGTERM):
        super().__init__(signum)
        self.signum = int(signum)

    @property
    def exit_code(self):
        return 128 + self.signum


INTERRUPT_SIGNALS = tuple(s for s in (getattr(signal, n, None) for n in ("SIGINT", "SIGTERM", "SIGHUP")) if s is not None)
_DEFERRED = {"depth": 0, "pending": None}


def _raise_for(signum):
    if signum == signal.SIGINT:
        raise KeyboardInterrupt()
    raise Terminated(signum)


def _on_signal(signum, frame):  # noqa: ARG001
    # Python runs handlers in the main thread only, whichever thread the OS
    # delivered the signal to, so recording here cannot miss a signal.
    if _DEFERRED["depth"]:
        if _DEFERRED["pending"] is None:
            _DEFERRED["pending"] = signum
        return
    _raise_for(signum)


def install_signal_handlers():
    """SIGINT and SIGTERM always get a handler. Python installs its SIGINT
    handler only if SIGINT was SIG_DFL at startup, and a background job of a
    non-interactive shell (`test-audit mutate ... &`) starts with SIGINT
    ignored: Ctrl-C/kill -INT was then silently dropped and a run went on to
    report a verdict. SIGHUP is handled unless it was inherited as ignored
    (`nohup` asked for that)."""
    for sig in INTERRUPT_SIGNALS:
        if sig == getattr(signal, "SIGHUP", None) and signal.getsignal(sig) == signal.SIG_IGN:
            continue
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):  # not main thread / unsupported
            pass


class Outcome:
    """What a command returns: an exit code, a JSON-able result, text lines."""

    def __init__(self, exit_code, result, text=None):
        self.exit_code = exit_code
        self.result = result
        self.text = text or []


def signal_exit(code):
    """Describe an exit that means "killed by a signal", else None.

    Negative codes come from the kernel. 128+N is the shell convention for a
    child killed by signal N (`sh -c 'cmd; exit $?'` turns SIGKILL into 137);
    it is treated the same when N is a real signal number on this system."""
    if code is None:
        return None
    if code < 0:
        return "killed by signal %d" % -code
    if code > 128:
        n = code - 128
        valid = signal.valid_signals() if hasattr(signal, "valid_signals") else range(1, 32)
        if n in {int(x) for x in valid}:
            return "exit %d = 128+%d: killed by signal %d (shell convention)" % (code, n, n)
    return None


def combine_exit(statuses):
    """1 if anything failed, else 2 if anything could not run, else 0."""
    statuses = list(statuses)
    if any(s == "failed" for s in statuses):
        return EXIT_FINDINGS
    if any(s in ("error", "unavailable") for s in statuses):
        return EXIT_ENV
    return EXIT_OK


# --------------------------------------------------------------------------
# text helpers

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r")


def strip_ansi(text):
    return _ANSI.sub("", text)


def tail_lines(text, n=40):
    lines = text.splitlines()
    return lines[-n:]


def posix(path):
    return path.replace(os.sep, "/")


def shell_join(argv):
    return " ".join(shlex.quote(a) for a in argv)


# --------------------------------------------------------------------------
# globs: `**` spans directories, `*` and `?` do not, `{a,b}` alternation,
# `[...]` classes. Patterns match whole repo-relative POSIX paths.

_GLOB_CACHE = {}


def _glob_body(pat):
    out = []
    i = 0
    n = len(pat)
    while i < n:
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:[^/]*/)*")
            i += 3
            continue
        if pat.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "{":
            depth, j = 1, i + 1
            while j < n and depth:
                if pat[j] == "{":
                    depth += 1
                elif pat[j] == "}":
                    depth -= 1
                j += 1
            if depth:
                out.append(re.escape(c))
            else:
                inner = pat[i + 1 : j - 1]
                alts, buf, d = [], "", 0
                for ch in inner:
                    if ch == "," and d == 0:
                        alts.append(buf)
                        buf = ""
                        continue
                    if ch == "{":
                        d += 1
                    elif ch == "}":
                        d -= 1
                    buf += ch
                alts.append(buf)
                out.append("(?:" + "|".join(_glob_body(a) for a in alts) + ")")
                i = j
                continue
        elif c == "[":
            j = pat.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                cls = pat[i + 1 : j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                out.append("[" + cls.replace("\\", "\\\\") + "]")
                i = j + 1
                continue
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def glob_regex(pat):
    rx = _GLOB_CACHE.get(pat)
    if rx is None:
        p = pat[2:] if pat.startswith("./") else pat
        if p.endswith("/"):
            p += "**"
        try:
            rx = re.compile(_glob_body(p) + r"\Z")
        except re.error as e:
            raise UsageError("invalid glob %r: %s" % (pat, e)) from None
        _GLOB_CACHE[pat] = rx
    return rx


def glob_match(path, patterns):
    for pat in patterns or ():
        if glob_regex(pat).match(path):
            return True
    return False


# --------------------------------------------------------------------------
# processes


class ProcResult:
    def __init__(self, argv, cwd):
        self.argv = argv
        self.cwd = cwd
        self.exit_code = None
        self.output = ""
        self.duration = 0.0
        self.error = None  # "not-found" | "timeout" | "interrupted" | message

    def tail(self, n=40):
        return tail_lines(strip_ansi(self.output), n)


def _proc_group_members(pgid):
    """Live (non-zombie) members of pgid from /proc, or None without /proc."""
    try:
        names = os.listdir("/proc")
    except OSError:
        return None
    if not os.path.exists("/proc/self/stat"):
        return None
    live = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name, "r") as f:
                stat = f.read()
        except OSError:
            continue
        # "pid (comm) S ppid pgrp ..."; comm may contain spaces and parens
        rest = stat[stat.rfind(")") + 2:].split()
        if len(rest) < 3:
            continue
        state, pgrp = rest[0], rest[2]
        if pgrp == str(pgid) and state not in ("Z", "X", "x"):
            live.append(int(name))
    return live


def _group_alive(pgid):
    """True while a non-zombie member of the group exists. Zombies are dead:
    they cannot run, and in a container without a reaping init they may
    never disappear."""
    members = _proc_group_members(pgid)
    if members is not None:
        return bool(members)
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class deferred_signals:
    """Hold SIGINT/SIGTERM/SIGHUP while cleanup must finish (group kill,
    restore): a signal that arrives inside is recorded and re-raised when the
    outermost section exits. This works at the Python-handler level, not with
    a thread signal mask, so it does not matter which thread the OS picks."""

    def __enter__(self):
        _DEFERRED["depth"] += 1
        return self

    def __exit__(self, *exc):
        _DEFERRED["depth"] -= 1
        if _DEFERRED["depth"] == 0 and _DEFERRED["pending"] is not None:
            signum, _DEFERRED["pending"] = _DEFERRED["pending"], None
            _raise_for(signum)
        return False


def _wait_group_gone(pgid, proc, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None and not _group_alive(pgid):
            return True
        time.sleep(0.05)
    return proc.poll() is not None and not _group_alive(pgid)


def kill_group(proc, grace=3.0):
    """Terminate the child's whole process group: SIGTERM, a grace period,
    then SIGKILL; return only once no member is left (or raise EnvError).

    The child runs in its own session (pgid == pid), so descendants that
    ignore SIGTERM are still reached. A descendant that calls setsid()
    itself leaves the group and cannot be tracked."""
    pgid = proc.pid
    if proc.poll() is not None and not _group_alive(pgid):
        return
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            pass
        if _wait_group_gone(pgid, proc, wait):
            return
    raise EnvError("process group %d survived SIGKILL" % pgid)


def run_process(argv, cwd, stream=None, timeout=None, env=None, max_capture=8 << 20):
    """Run argv without a shell; merge stderr into stdout; optionally tee.

    The child gets its own session so Ctrl-C/SIGTERM reach us first and we
    tear down the whole process group before re-raising.
    """
    res = ProcResult(list(argv), cwd)
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=full_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except FileNotFoundError:
        res.error = "not-found"
        res.output = "test-audit: command not found: %s\n" % argv[0]
        return res
    except PermissionError:
        res.error = "not-executable"
        res.output = "test-audit: not executable: %s\n" % argv[0]
        return res
    except ValueError as e:  # e.g. an embedded NUL byte
        raise UsageError("cannot run %r: %s" % (argv, e)) from None

    chunks = []
    size = [0]

    def reader():
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            data = os.read(proc.stdout.fileno(), 65536)
            if not data:
                break
            text = dec.decode(data)
            if stream is not None and text:
                try:
                    stream.write(text)
                    stream.flush()
                except (OSError, ValueError):
                    pass
            chunks.append(text)
            size[0] += len(text)
            while size[0] > max_capture and len(chunks) > 1:
                size[0] -= len(chunks.pop(0))
        tail = dec.decode(b"", final=True)
        if tail:
            chunks.append(tail)

    t = threading.Thread(target=reader, daemon=True, name="test-audit-reader")
    t.start()
    try:
        # Wait in short slices instead of one blocking waitpid. The OS may
        # deliver a process-directed signal to the reader thread; its C-level
        # handler only flags it, and Python runs the handler in the main
        # thread at the next bytecode. A blocking waitpid would postpone that
        # until the child exits (a keeper running 60 s turned Ctrl-C into a
        # verdict); slices bound the delay to 0.2 s, whichever thread got it.
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            step = 0.2 if deadline is None else max(0.0, min(0.2, deadline - time.monotonic()))
            try:
                proc.wait(timeout=step)
                break
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() >= deadline:
                    res.error = "timeout"
                    break
        # Leftover descendants (background servers, ignored SIGTERM) belong
        # to this run: never let them outlive it and touch the tree later.
        with deferred_signals():
            kill_group(proc)
    except BaseException:
        with deferred_signals():  # a second Ctrl-C must not cut the teardown short
            kill_group(proc, grace=1.5)
        t.join(2)
        raise
    t.join(10)
    try:
        proc.stdout.close()
    except OSError:
        pass
    res.duration = round(time.monotonic() - start, 3)
    res.exit_code = proc.returncode
    res.output = "".join(chunks)
    return res


def capture(argv, cwd, timeout=120, env=None):
    """Quick helper for probes: returns (exit_code or None, stripped output)."""
    r = run_process(argv, cwd, timeout=timeout, env=env)
    return r.exit_code if r.error is None else None, strip_ansi(r.output)


# --------------------------------------------------------------------------
# git


def git(repo, *args, check=True):
    try:
        p = subprocess.run(
            ["git", "-C", repo] + list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        raise EnvError("git is not installed or not on PATH") from None
    out = p.stdout.decode("utf-8", "surrogateescape")
    if check and p.returncode != 0:
        raise EnvError(
            "git %s failed: %s" % (" ".join(args), p.stderr.decode("utf-8", "replace").strip())
        )
    if not check:
        return p.returncode, out
    return out


def git_toplevel(path):
    try:
        p = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        raise EnvError("git is not installed or not on PATH") from None
    if p.returncode != 0:
        raise UsageError("not inside a git repository: %s (pass --repo)" % path)
    return os.path.realpath(p.stdout.decode().strip())


def split_z(out):
    return [x for x in out.split("\0") if x]


def ref_exists(repo, ref):
    code, _ = git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False)
    return code == 0


def _merge_base(repo, ref):
    code, out = git(repo, "merge-base", "HEAD", ref, check=False)
    return out.strip() if code == 0 and out.strip() else None


def _no_merge_base(ref, repo):
    _, out = git(repo, "rev-parse", "--is-shallow-repository", check=False)
    hint = (
        "the clone is shallow: deepen it with `git fetch --deepen=200 origin` or `git fetch --unshallow`"
        if out.strip() == "true" else "the histories share no commit"
    )
    return UsageError(
        "no merge-base between HEAD and %s (%s). test-audit never falls back to a direct diff; "
        "fix the history, or pass --base with a commit that is an ancestor of HEAD" % (ref, hint)
    )


def resolve_base(ctx, base=None):
    """Return (base_sha, description): always merge-base(HEAD, ref)."""
    repo = ctx.repo
    if not ref_exists(repo, "HEAD"):
        raise UsageError("repository has no commits yet; nothing to diff against")
    if base:
        if not ref_exists(repo, base):
            raise UsageError("base ref not found: %s" % base)
        sha = _merge_base(repo, base)
        if not sha:
            raise _no_merge_base(base, repo)
        return sha, "merge-base(HEAD, %s)" % base
    candidates = []
    cfg_base = ctx.config.get("baseBranch")
    if cfg_base:
        candidates.append(cfg_base)
    code, out = git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False)
    if code == 0 and out.strip():
        candidates.append(out.strip().replace("refs/remotes/", "", 1))
    candidates += ["origin/main", "origin/master", "main", "master"]
    for cand in candidates:
        if ref_exists(repo, cand):
            sha = _merge_base(repo, cand)
            if not sha:
                raise _no_merge_base(cand, repo)
            return sha, "merge-base(HEAD, %s)" % cand
    raise UsageError(
        "cannot find a default branch (tried %s); pass --base REF" % ", ".join(candidates)
    )


# --------------------------------------------------------------------------
# context


def _shell_contexts(text):
    """Per character: (state, escaped) when text is read as POSIX shell
    source. state is None (unquoted), "'", '"' or "`"."""
    n = len(text)
    ctx_at, esc = [None] * n, [False] * n
    state, i = None, 0
    while i < n:
        c = text[i]
        ctx_at[i] = state
        if c == "\\" and state in (None, '"', "`"):
            if i + 1 < n:
                ctx_at[i + 1] = state
                esc[i + 1] = True
            i += 2
            continue
        if state is None:
            if c in "'\"`":
                state = c
        elif c == state:
            state = None
        i += 1
    return ctx_at, esc


def placeholder_problems(text, pattern):
    """Every site `pattern` would substitute in text, paired with why it is
    unsafe. Validation and substitution use the same regex over the same
    text, so no site can be expanded without having been checked. A value
    is substituted shell-quoted, which is literal only in unquoted,
    unescaped context; anything else could let repo-controlled text (a
    package path or file name) become shell syntax."""
    ctx_at, esc = _shell_contexts(text)
    out = []
    for m in pattern.finditer(text):
        i = m.start()
        if esc[i]:
            out.append((m.group(0), "is escaped with a backslash"))
        elif ctx_at[i] is not None:
            out.append((m.group(0), "is inside %s" % {"'": "single quotes", '"': "double quotes", "`": "backticks"}[ctx_at[i]]))
        elif i > 0 and text[i - 1] == "$":
            out.append((m.group(0), "follows $"))
    return out


def substitute(text, pattern, values, where):
    """Refuse unsafe sites, then replace every site in one pass (inserted
    text is never rescanned). `values` must already be shell-quoted."""
    bad = placeholder_problems(text, pattern)
    if bad:
        ph, why = bad[0]
        raise UsageError(
            "%s: %s %s in %r. test-audit shell-quotes substituted values itself, so put the placeholder "
            "in plain unquoted, unescaped position (e.g. sh -c 'cd /srv && {cmd}')" % (where, ph, why, text))
    return pattern.sub(lambda m: values[m.group(1)], text)


class Ctx:
    def __init__(self, repo, config, config_path, via, dry_run, json_mode, timeout=None):
        self.repo = repo
        self.config = config
        self.config_path = config_path
        self.via_raw = via
        try:
            self.via = shlex.split(via) if via else None
        except ValueError as e:
            raise UsageError("--via is not a valid command prefix (%s): %r" % (e, via)) from None
        if via is not None and not self.via:
            raise UsageError("--via is empty")
        for tok in self.via or ():
            substitute(tok, _PLACEHOLDER, {"cmd": "", "root": ""}, "--via")  # validate up front
        self.dry_run = dry_run
        self.json = json_mode
        self.timeout = timeout
        self.warnings = []
        self.cache = {}
        self.out = sys.stdout
        self.err = sys.stderr

    def temp_parent(self):
        """The temp root, checked before anything is created in it."""
        import tempfile

        parent = os.path.realpath(tempfile.gettempdir())
        if self.inside(parent):
            raise EnvError("the temp dir %s is inside the repo; set TMPDIR outside it" % parent)
        return parent

    def mkdtemp(self, prefix, register=True):
        import tempfile

        d = os.path.realpath(tempfile.mkdtemp(prefix=prefix, dir=self.temp_parent()))
        if register:
            self.cache.setdefault("tmpdirs", []).append(d)
        return d

    def _mkdtemp(self, prefix):
        return self.mkdtemp(prefix)

    def fresh_pycache(self):
        """(env, dir): a new, empty Python bytecode cache outside the repo."""
        d = self._mkdtemp("test-audit-pycache-")
        return {"PYTHONPYCACHEPREFIX": d}, d

    def scratch(self, name):
        """A per-invocation temp dir outside the repo (tool caches)."""
        key = ("scratch", name)
        if key not in self.cache:
            self.cache[key] = self._mkdtemp("test-audit-%s-" % name)
        return self.cache[key]

    def drop(self, d):
        import shutil

        shutil.rmtree(d, ignore_errors=True)
        if d in self.cache.get("tmpdirs", []):
            self.cache["tmpdirs"].remove(d)

    def cleanup(self):
        import shutil

        for d in self.cache.pop("tmpdirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    def abspath(self, rel):
        return self.repo if rel in ("", ".") else os.path.join(self.repo, *rel.split("/"))

    def relpath(self, path):
        real = os.path.realpath(path)
        rel = os.path.relpath(real, self.repo)
        return "." if rel == "." else posix(rel)

    def inside(self, path):
        real = os.path.realpath(path)
        return real == self.repo or real.startswith(self.repo + os.sep)

    def files(self):
        """Tracked + untracked-not-ignored files that exist, sorted."""
        if "files" not in self.cache:
            out = git(self.repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
            seen = set()
            for p in split_z(out):
                if p in seen:
                    continue
                full = self.abspath(p)
                if os.path.isfile(full) or (os.path.islink(full) and not os.path.isdir(full)):
                    seen.add(p)
            self.cache["files"] = sorted(seen)
        return self.cache["files"]

    def resolve_user_path(self, arg, must_exist=True):
        """CLI paths: relative to cwd first, then to the repo root."""
        cands = [arg] if os.path.isabs(arg) else [os.path.abspath(arg), os.path.join(self.repo, arg)]
        for c in cands:
            if os.path.lexists(c):
                if not self.inside(c):
                    raise UsageError("path is outside the repository: %s" % arg)
                return self.relpath(c)
        if must_exist:
            raise UsageError("no such file: %s" % arg)
        c = cands[-1]
        if not self.inside(os.path.dirname(c) or "."):
            raise UsageError("path is outside the repository: %s" % arg)
        return posix(os.path.relpath(os.path.abspath(c), self.repo))

    def exec_cwd(self, root_rel):
        """Local cwd for a command: the owning root, or the repo root under
        --via (transports such as Crabbox sync and run from the repo root)."""
        return self.repo if self.via else self.abspath(root_rel)

    def shown_cwd(self, root_rel):
        return "." if self.via else root_rel

    def wrap(self, argv, root_rel="."):
        """Apply --via. The command is started from the repo root; for a
        non-top root it first enters the root through a fixed shell script
        that takes the root and argv as positional parameters, so no path is
        ever spliced into shell source:

            PREFIX sh -c 'cd -- "$1" && shift && exec "$@"' test-audit ROOT ARGV...

        `{cmd}` in PREFIX is replaced by that whole command shell-quoted as one
        word (for ssh-style transports that re-parse a string); `{root}` is
        replaced by the shell-quoted root. Otherwise the words are appended."""
        if not self.via:
            return list(argv)
        inner = list(argv) if root_rel in ("", ".") else (
            ["sh", "-c", ENTER_ROOT, "test-audit", root_rel] + list(argv))
        values = {"root": shlex.quote(root_rel), "cmd": shell_join(inner)}
        used_cmd = any("{cmd}" in tok for tok in self.via)
        # one pass over the original template: inserted text is never rescanned,
        # so a path containing "{cmd}" or "{root}" stays literal
        out = [substitute(tok, _PLACEHOLDER, values, "--via") for tok in self.via]
        if not used_cmd:
            out += inner
        return out

    def stream(self):
        """Where runner output streams while it runs (captured only in --json)."""
        return None if self.json else self.out

    def progress(self, msg):
        try:
            self.err.write(msg + "\n")
            self.err.flush()
        except (OSError, ValueError):
            pass
