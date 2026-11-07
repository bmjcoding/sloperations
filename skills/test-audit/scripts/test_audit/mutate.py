"""mutate: one deliberate mutation of a production owner, keeper tests, and a
byte-for-byte restore verified by sha256."""

import difflib
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile

from . import runners
from .core import EXIT_ENV, EXIT_FINDINGS, EXIT_OK, EnvError, Outcome, UsageError, deferred_signals, shell_join


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _patch_paths(text):
    paths = set()
    for line in text.splitlines():
        if line.startswith(("--- ", "+++ ")):
            p = line[4:].split("\t", 1)[0].strip()
            if p == "/dev/null":
                continue
            if p.startswith(("a/", "b/")):
                p = p[2:]
            paths.add(p)
    return paths


def _apply_patch(ctx, target_rel, original, patch_path):
    with open(patch_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    paths = _patch_paths(text)
    if not paths:
        raise UsageError("patch has no file headers (---/+++)")
    if paths != {target_rel}:
        raise UsageError("patch must touch only %s (it touches %s)" % (target_rel, ", ".join(sorted(paths))))
    tmp = ctx.mkdtemp("test-audit-patch-", register=False)
    try:
        dst = os.path.join(tmp, *target_rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(original)
        p = subprocess.run(["git", "apply", "--whitespace=nowarn", os.path.abspath(patch_path)], cwd=tmp,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode != 0:
            raise UsageError("patch does not apply: %s" % p.stderr.decode("utf-8", "replace").strip())
        with open(dst, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _replace(original, old, new, occurrence):
    o, n = old.encode("utf-8"), new.encode("utf-8")
    if not o:
        raise UsageError("--replace must not be empty")
    starts = []
    i = original.find(o)
    while i != -1:
        starts.append(i)
        i = original.find(o, i + len(o))
    if not starts:
        raise UsageError("--replace text not found in file")
    if occurrence is None:
        if len(starts) != 1:
            raise UsageError("--replace text matches %d times; pass --occurrence N (1-%d)" % (len(starts), len(starts)))
        at = starts[0]
    else:
        if not 1 <= occurrence <= len(starts):
            raise UsageError("--occurrence %d out of range (1-%d)" % (occurrence, len(starts)))
        at = starts[occurrence - 1]
    return original[:at] + n + original[at + len(o):], original.count(b"\n", 0, at) + 1


def _custom_plans(ctx, template, tests):
    def mk(argv, label):
        p = runners.Plan(label)
        p.runner = "custom"
        p.cwd_rel = "."
        p.argv = argv
        p.error_exit_codes = tuple(ctx.config.get("testErrorExitCodes", []))
        return p

    if "{files}" in template:
        return [mk(runners.expand_template(template, tests, "."), " ".join(tests))]
    if "{file}" in template:
        return [mk(runners.expand_template(template, [t], "."), t) for t in tests]
    return [mk(runners.expand_template(template, [], "."), "(test-cmd)")]


def _run_all(ctx, plans, phase):
    # Each run gets an empty bytecode cache: a same-size mutation written in
    # the same mtime second would otherwise load the original's stale .pyc.
    out = []
    for i, p in enumerate(plans, 1):
        out.append(runners.execute(ctx, p, label="%s [%d/%d]" % (phase, i, len(plans))))
    return out


_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_parent(ctx, target, create):
    """Open the target's parent through a chain of dir_fds from the repo
    root, one component at a time with O_NOFOLLOW: every component must be a
    real directory (not a symlink), so nothing can steer a write outside the
    repo, even if a keeper swaps a directory between checks. Missing
    components are created (restore only) inside the verified chain.
    Returns (dir_fd, basename); raises EnvError if the chain is unsafe."""
    parts = target.split("/")
    fd = os.open(ctx.repo, _DIR_FLAGS)
    try:
        for i, part in enumerate(parts[:-1]):
            try:
                nfd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise EnvError("directory %s disappeared" % "/".join(parts[: i + 1])) from None
                os.mkdir(part, 0o777, dir_fd=fd)
                nfd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except OSError as e:  # ELOOP (symlink), ENOTDIR (replaced by a file)
                raise EnvError("path component %s is no longer a real directory (%s)"
                               % ("/".join(parts[: i + 1]), e.strerror)) from None
            os.close(fd)
            fd = nfd
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _safe_write(ctx, target, data, mode, times=None, create=False):
    """Write a fresh regular file in the verified parent dir, then rename it
    over the target name. rename() replaces whatever is at that name (file,
    symlink, nothing) without following it."""
    dfd, base = _open_parent(ctx, target, create)
    tmp = ".test-audit-%s.tmp" % os.urandom(6).hex()
    try:
        tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=dfd)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(tfd, view):]
            os.fchmod(tfd, mode)
            os.fsync(tfd)
        finally:
            os.close(tfd)
        if times:
            os.utime(tmp, ns=times, dir_fd=dfd, follow_symlinks=False)
        os.rename(tmp, base, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise
    finally:
        os.close(dfd)


def _verify(ctx, target, sha, mode):
    try:
        dfd, base = _open_parent(ctx, target, create=False)
    except (OSError, EnvError):
        return False
    try:
        st = os.stat(base, dir_fd=dfd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != mode:
            return False
        ffd = os.open(base, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dfd)
        try:
            chunks = []
            while True:
                b = os.read(ffd, 1 << 20)
                if not b:
                    break
                chunks.append(b)
        finally:
            os.close(ffd)
        return _sha(b"".join(chunks)) == sha
    except OSError:
        return False
    finally:
        os.close(dfd)


def _supplied_path(ctx, arg):
    """The path as the user typed it (not resolved), cwd first then repo."""
    for c in ([arg] if os.path.isabs(arg) else [os.path.abspath(arg), os.path.join(ctx.repo, arg)]):
        if os.path.lexists(c):
            return c
    return None


def cmd_mutate(ctx, args):
    if ctx.via:
        raise UsageError(
            "mutate does not support --via: it edits the local tree, and a transport may run a stale, "
            "unsynced, or differently cached copy. Run the whole CLI inside the remote checkout instead.")
    supplied = _supplied_path(ctx, args.file)
    if supplied and os.path.islink(supplied):
        raise UsageError("--file is a symlink; pass the real file it points to: %s" % args.file)
    target = ctx.resolve_user_path(args.file)
    full = ctx.abspath(target)
    if not os.path.isfile(full) or os.path.islink(full):
        raise UsageError("--file must be a regular file inside the repo: %s" % args.file)
    if target.startswith(".git/") or "/.git/" in target:
        raise UsageError("refusing to mutate inside .git")
    if (args.replace is None) == (args.patch is None):
        raise UsageError("give exactly one of --replace/--with or --patch")
    if args.replace is not None and args.with_ is None:
        raise UsageError("--replace needs --with")
    tests = [ctx.resolve_user_path(t) for t in args.tests]
    with open(full, "rb") as f:
        original = f.read()
    sha_before = _sha(original)
    if args.patch:
        mutated, line = _apply_patch(ctx, target, original, args.patch), None
    else:
        mutated, line = _replace(original, args.replace, args.with_, args.occurrence)
    if mutated == original:
        raise UsageError("mutation does not change the file")
    diff = list(difflib.unified_diff(
        original.decode("utf-8", "replace").splitlines(), mutated.decode("utf-8", "replace").splitlines(),
        "a/" + target, "b/" + target, n=1, lineterm=""))
    if args.test_cmd:
        plans = _custom_plans(ctx, args.test_cmd, tests)
    else:
        plans = [runners.plan_for(ctx, t) for t in tests]
    result = {"file": target, "sha256": sha_before, "line": line, "diff": diff, "tests": tests,
              "commands": [ctx.wrap(p.argv, p.cwd_rel) if p.argv else None for p in plans]}
    if ctx.dry_run:
        result["verdict"] = "DRY-RUN"
        text = diff + ["(cd %s && %s)" % (p.cwd_rel, shell_join(c)) if c else "# %s" % p.error for p, c in zip(plans, result["commands"])]
        return Outcome(EXIT_OK, result, text)
    if any(p.error for p in plans):
        result["verdict"] = "ERROR"
        result["reason"] = "; ".join(p.error for p in plans if p.error)
        return Outcome(EXIT_ENV, result, ["ERROR: " + result["reason"]])

    if not args.skip_precheck:
        pre = _run_all(ctx, plans, "precheck")
        result["precheck"] = pre
        bad = [r for r in pre if r["status"] != "passed"]
        if bad:
            result["verdict"] = "ERROR"
            result["reason"] = "keeper tests do not pass before mutation: " + ", ".join("%s (%s)" % (r["file"], r["status"]) for r in bad)
            return Outcome(EXIT_ENV, result, ["ERROR: " + result["reason"]])

    st = os.lstat(full)
    mode = stat.S_IMODE(st.st_mode)
    times = (st.st_atime_ns, st.st_mtime_ns)
    fd, backup = tempfile.mkstemp(prefix="test-audit-mutate-", suffix=".orig", dir=ctx.temp_parent())
    with os.fdopen(fd, "wb") as f:
        f.write(original)
    ctx.progress("mutate: original saved to %s (sha256 %s)" % (backup, sha_before))
    restored_ok = False
    post = []
    restore_error = None
    try:
        _safe_write(ctx, target, mutated, mode)
        post = _run_all(ctx, plans, "mutated")  # each run's process group is gone when it returns
    finally:
        with deferred_signals():  # an interrupt must not cut the restore short
            try:
                _safe_write(ctx, target, original, mode, times, create=True)
            except (OSError, EnvError) as e:
                restore_error = str(e)
                ctx.progress("mutate: restore refused, nothing written: %s" % e)
            restored_ok = _verify(ctx, target, sha_before, mode)
            if restored_ok:
                os.unlink(backup)
            else:
                ctx.progress("mutate: RESTORE FAILED; original bytes are in %s" % backup)
    if not restored_ok:
        result.update(results=post, restored=False, backup=backup, verdict="ERROR",
                      reason="could not restore %s (%s); original bytes saved at %s"
                      % (target, restore_error or "verification failed", backup))
        return Outcome(EXIT_ENV, result, ["ERROR: " + result["reason"]])
    result["results"] = post
    result["restored"] = True
    statuses = [r["status"] for r in post]
    if any(s == "failed" for s in statuses):
        verdict, code = "CAUGHT", EXIT_OK
    elif any(s == "error" for s in statuses):
        verdict, code = "ERROR", EXIT_ENV
    else:
        verdict, code = "MISSED", EXIT_FINDINGS
    result["verdict"] = verdict
    text = diff + ["%s %s%s" % (r["status"].upper(), r["file"], " (%s)" % r["reason"] if r["reason"] else "") for r in post]
    text.append("%s (restored %s, sha256 %s)" % (verdict, target, sha_before[:16]))
    return Outcome(code, result, text)
