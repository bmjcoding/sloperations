"""skill-sync: vendor upstream agent-skill folders through pinned, anchored rules."""
from __future__ import annotations

import argparse
import contextlib
import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

from . import engine, sandbox
from .config import (EXIT_ERROR, EXIT_FAIL, EXIT_NOTHING_NEWER, EXIT_OK, EXIT_USAGE, SHA_RE, ConfigError,
                     Fork, SkillSyncError, fork_path, list_forks, load_fork)
from .gitcache import Repo, cache_root, github_verified

TOOL_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = "tooling/skill-sync/skill-sync"
IGNORED_NAMES = {"__pycache__", ".DS_Store"}
TEMP_MARK = ".skill-sync-"


class Context:
    def __init__(self, root: Path, cache: Optional[str]):
        self.root = root
        self._cache_arg = cache
        self._cache: Optional[Path] = None

    @property
    def cache(self) -> Path:
        if self._cache is None:
            self._cache = cache_root(self._cache_arg, self.root)
        return self._cache

    def fork(self, name: str, mode: Optional[str] = None) -> Fork:
        fork = load_fork(self.root, name)
        if mode and fork.mode != mode:
            hint = "watch" if fork.mode == "watch" else "check/sync"
            raise ConfigError(f"{name} is a {fork.mode}-mode fork; use `skill-sync {hint} {name}`")
        return fork

    def repo(self, fork: Fork) -> Repo:
        return Repo(self.cache, fork.upstream)

    @contextlib.contextmanager
    def repo_lock(self) -> Iterator[None]:
        """Serializes changes to this repository's pins and outputs (the lock lives in the cache)."""
        locks = self.cache / "locks"
        locks.mkdir(exist_ok=True)
        digest = hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
        with open(locks / f"{digest}.lock", "w") as handle:
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def verify_backend(self, fork: Fork, args) -> str:
        """The sandbox for this fork's verify commands; refuses early when none is available."""
        if not fork.verify:
            return sandbox.UNSANDBOXED
        backend = sandbox.choose_backend(bool(getattr(args, "unsandboxed_verify", False)))
        if backend == sandbox.UNSANDBOXED:
            say("warning: --unsandboxed-verify: upstream verify commands run without a sandbox "
                "(only for disposable CI runners with no secrets)")
        return backend

    def run_verify(self, build: engine.Build, work: Path, backend: str) -> List[engine.Check]:
        return engine.run_verify(build, work, backend, [str(self.root), str(self.cache)], self.cache / "logs")


@contextlib.contextmanager
def workdir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="skill-sync-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def say(text: str = "") -> None:
    print(text, flush=True)


# ------------------------------------------------------------------ reports --

MARK = {"pass": "PASS", "fail": "FAIL", "left-out": "LEFT OUT", "skipped": "SKIP"}


def render_checks(checks: List[engine.Check]) -> List[str]:
    lines = []
    width = max((len(c.id) for c in checks), default=10)
    for c in checks:
        tag = MARK[c.status]
        if c.status == "fail":
            tag += " (strict)" if c.strict else f" (feature {c.feature})"
        first, *rest = (c.message or "").split("\n")
        lines.append(f"  {tag:<24} {c.id:<{width}}  {first}")
        lines.extend(f"  {'':<24} {'':<{width}}  {r}" for r in rest)
    return lines


def short(sha: Optional[str]) -> str:
    return sha[:7] if sha else "-"


def write_outputs(args, report_lines: List[str], data: dict) -> None:
    if getattr(args, "report", None):
        Path(args.report).write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    if getattr(args, "json", None):
        data = dict(data, report="\n".join(report_lines) + "\n")
        Path(args.json).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------- list --

def cmd_list(ctx: Context, args) -> int:
    for name in list_forks(ctx.root):
        fork = ctx.fork(name)
        target = ", ".join(p.output for p in fork.paths) if fork.mode == "rules" else (fork.local or "-")
        say(f"{name}\t{fork.mode}\t{short(fork.upstream.sha)}\t{target}")
    return EXIT_OK


# --------------------------------------------------------------------- sync --

def _recover(dest: Path) -> None:
    """Finishes or undoes a swap an interrupted run left behind."""
    parent = dest.parent
    if not parent.is_dir():
        return
    olds = sorted(parent.glob(f".{dest.name}{TEMP_MARK}old-*"))
    if olds and not dest.exists():
        os.rename(olds.pop(), dest)
    for stray in olds + sorted(parent.glob(f".{dest.name}{TEMP_MARK}new-*")):
        shutil.rmtree(stray, ignore_errors=True)


JOURNAL = ".skill-sync-txn.json"


def _journal(root: Path) -> Path:
    return root / "upstream" / JOURNAL


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}{TEMP_MARK}{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _rollback(root: Path, txn: dict) -> None:
    """Puts every output and the manifest back exactly as they were before the transaction."""
    for entry in reversed(txn["outputs"]):
        dest, new, old = root / entry["dest"], root / entry["new"], root / entry["old"]
        if old.exists():
            if dest.exists():
                shutil.rmtree(dest)
            os.rename(old, dest)
        elif not entry["existed"] and dest.exists() and not new.exists():
            shutil.rmtree(dest)  # it was created by this transaction
        if new.exists():
            shutil.rmtree(new, ignore_errors=True)
    manifest = txn.get("manifest")
    if manifest:
        path = root / manifest["path"]
        if path.read_text(encoding="utf-8") != manifest["old_text"]:
            _write_json_text(path, manifest["old_text"])
        tmp = root / manifest["tmp"]
        if tmp.exists():
            tmp.unlink()
    _journal(root).unlink()


def _write_json_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}{TEMP_MARK}{secrets.token_hex(4)}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _roll_forward(root: Path, txn: dict) -> None:
    for entry in txn["outputs"]:
        shutil.rmtree(root / entry["old"], ignore_errors=True)
        shutil.rmtree(root / entry["new"], ignore_errors=True)
    _journal(root).unlink()


def recover(root: Path) -> Optional[str]:
    """Finishes (committed) or undoes (prepared) a transaction an interrupted run left behind."""
    path = _journal(root)
    if not path.exists():
        return None
    txn = json.loads(path.read_text(encoding="utf-8"))
    if txn.get("state") == "committed":
        _roll_forward(root, txn)
        return "finished an interrupted update"
    _rollback(root, txn)
    return "rolled back an interrupted update"


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def publish(ctx: Context, fork: Fork, out_root: Path, outputs: List[str],
            pin: Optional[Tuple[Path, str, str]] = None) -> None:
    """Swaps in every output folder and, for bump, the new pin, as one recoverable transaction.

    Every destination is checked before anything changes. A journal in upstream/
    records each step, so a failure (or a killed process, on the next run) puts the
    old outputs and the old pin back; once the journal says "committed", the next
    run finishes the cleanup instead.
    """
    root = ctx.root
    with ctx.repo_lock():
        # The build ran before this lock: if the definition (pin or rules) changed since,
        # say a concurrent bump landed, these outputs are stale. Publish nothing.
        definition = fork_path(root, fork.name)
        current = definition.read_text(encoding="utf-8") if definition.is_file() else None
        if current != fork.source_text:
            raise SkillSyncError(f"upstream/{fork.name}.json changed while this run was building "
                                 f"(another sync or bump?); nothing was published. Re-run it.", EXIT_FAIL)
        note = recover(root)
        if note:
            say(f"note: {note}")
        # Preflight: nothing is written until every destination is known to be replaceable.
        plan = []
        for output in outputs:
            dest = root / output
            for ancestor in [dest, *dest.parents]:
                if ancestor == root:
                    break
                if ancestor.is_symlink() or (ancestor.exists() and not ancestor.is_dir()):
                    raise SkillSyncError(f"{_rel(root, ancestor)} exists and is not a folder; nothing was changed")
            _recover(dest)
            parent = dest.parent
            existing = parent
            while not existing.exists():
                existing = existing.parent
            if not os.access(existing, os.W_OK):
                raise SkillSyncError(f"{_rel(root, existing) or '.'} is not writable; nothing was changed")
            token = secrets.token_hex(4)
            plan.append({"dest": output, "existed": dest.exists(),
                         "new": _rel(root, parent / f".{dest.name}{TEMP_MARK}new-{token}"),
                         "old": _rel(root, parent / f".{dest.name}{TEMP_MARK}old-{token}")})
        manifest = None
        if pin:
            path, old_sha, new_sha = pin
            old_text = path.read_text(encoding="utf-8")
            needle = f'"sha": "{old_sha}"'
            if path.is_symlink() or old_text.count(needle) != 1:
                raise SkillSyncError(f"{_rel(root, path)}: expected exactly one {needle}; nothing was changed", EXIT_USAGE)
            if not os.access(path.parent, os.W_OK):
                raise SkillSyncError(f"{_rel(root, path.parent)} is not writable; nothing was changed")
            tmp = path.with_name(f".{path.name}{TEMP_MARK}{secrets.token_hex(4)}")
            manifest = {"path": _rel(root, path), "tmp": _rel(root, tmp), "old_text": old_text,
                        "new_text": old_text.replace(needle, f'"sha": "{new_sha}"')}
        txn = {"state": "prepared", "outputs": plan, "manifest": manifest}
        _write_json_atomic(_journal(root), txn)
        try:
            for entry in plan:
                new = root / entry["new"]
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(out_root / entry["dest"], new, symlinks=False)
                os.chmod(new, 0o755)
            if manifest:
                (root / manifest["tmp"]).write_text(manifest["new_text"], encoding="utf-8")
            for entry in plan:
                dest = root / entry["dest"]
                if dest.exists():
                    os.rename(dest, root / entry["old"])
                os.rename(root / entry["new"], dest)
            if manifest:
                os.replace(root / manifest["tmp"], root / manifest["path"])
            txn["state"] = "committed"
            _write_json_atomic(_journal(root), txn)
        except BaseException:
            _rollback(root, txn)
            raise
        _roll_forward(root, txn)


def _build_at_pin(ctx: Context, fork: Fork, work: Path) -> engine.Build:
    repo = ctx.repo(fork)
    with repo.locked():
        repo.ensure_commit(fork.upstream.sha)
        return engine.build(repo, fork, fork.upstream.sha, work)


def cmd_sync(ctx: Context, args) -> int:
    fork = ctx.fork(args.name, "rules")
    backend = ctx.verify_backend(fork, args) if args.checks else None
    with workdir() as work:
        build = _build_at_pin(ctx, fork, work)
        checks = list(build.checks)
        if backend and build.ok:
            checks += ctx.run_verify(build, work, backend)
        say(f"skill-sync sync {fork.name} at {fork.upstream.sha}")
        for line in render_checks(checks):
            say(line)
        failed = [c for c in checks if c.status == "fail" and c.strict]
        if failed:
            say(f"\nFAILED: {', '.join(c.id for c in failed)}. Nothing was written; "
                f"{', '.join(p.output for p in fork.paths)} is unchanged.")
            return EXIT_FAIL
        outputs = engine.assemble(build, work / "out")
        publish(ctx, fork, work / "out", outputs)
    _note_left_out(build)
    say(f"\nwrote {', '.join(outputs)}")
    return EXIT_OK


def _note_left_out(build: engine.Build) -> None:
    for name, why in sorted(build.left_out.items()):
        say(f"note: feature {name} left out ({why})")


# -------------------------------------------------------------------- check --

def evaluate(ctx: Context, fork: Fork, to: Optional[str], work: Path, backend: str) -> dict:
    """Evaluates the newest candidate (or `to`) in `work`. Writes nothing to the repository."""
    pin = fork.upstream.sha
    repo = ctx.repo(fork)
    result: dict = {"name": fork.name, "mode": fork.mode, "upstream": fork.upstream.web_url,
                    "branch": fork.upstream.branch, "pin": pin, "candidate": None, "status": None,
                    "commits": [], "checks": [], "failed_strict": [], "left_out": {}, "adopt_command": None}
    with repo.locked():
        tip = repo.sync(pin)
        if to:
            candidate = repo.fetch_ref(to)
        else:
            # Along the branch's first-parent history, so a merged topic yields the mainline merge.
            candidate = repo.newest_touching(tip, fork.tracked_paths(), exclude=pin) or pin
        result["tip"] = tip
        if candidate == pin or (not to and repo.is_ancestor(candidate, pin)):
            result["status"] = "current"
            return result
        result["candidate"] = candidate
        result["candidate_info"] = repo.describe(candidate)
        checks: List[engine.Check] = []
        if not repo.is_ancestor(pin, candidate):
            checks.append(engine.Check("ancestry", "ancestry", "fail",
                                       message=f"{short(candidate)} is not a descendant of the pin {short(pin)}; pins only move forward"))
        else:
            result["commits"] = repo.commits_between(pin, candidate, fork.tracked_paths())
        if fork.upstream.require_signed:
            owner, name = fork.upstream.github
            verified, reason = github_verified(owner, name, candidate)
            checks.append(engine.Check("signature", "signature", "pass" if verified else "fail",
                                       message=f"GitHub verification: {reason}"))
        if fork.mode == "rules":
            build = engine.build(repo, fork, candidate, work)
            checks += build.checks
            result["left_out"] = build.left_out
            if build.ok and not any(c.status == "fail" and c.strict for c in checks):
                checks += ctx.run_verify(build, work, backend)
            elif fork.verify:
                checks += [engine.Check(f"verify:{v.name}", "verify", "skipped", message="skipped: a strict check failed")
                           for v in fork.verify]
            result["_build"] = build
    result["checks"] = [c.as_dict() for c in checks]
    result["failed_strict"] = [c.id for c in checks if c.status == "fail" and c.strict]
    result["status"] = "fails" if result["failed_strict"] else "ready"
    if result["status"] == "ready":
        result["adopt_command"] = f"{LAUNCHER} bump {fork.name} --to {candidate}"
    result["_checks"] = checks
    return result


def check_report(fork: Fork, result: dict) -> List[str]:
    lines = [f"skill-sync check {fork.name}",
             f"  upstream   {result['upstream']} ({result['branch']})",
             f"  pin        {result['pin']}"]
    if result["status"] == "current":
        lines += [f"  candidate  none: no commit after the pin touches {', '.join(fork.tracked_paths())}",
                  "", "RESULT: nothing newer; the pin is current."]
        return lines
    info = result.get("candidate_info", {})
    lines.append(f"  candidate  {result['candidate']} ({info.get('date', '')}) {info.get('subject', '')}")
    commits = result["commits"]
    if commits:
        lines += ["", f"Upstream commits since the pin touching tracked paths ({len(commits)}):"]
        lines += [f"  {short(c['sha'])} {c['date']} {c['subject']}" for c in commits[:50]]
        if len(commits) > 50:
            lines.append(f"  ... and {len(commits) - 50} more")
    lines += ["", "Checks:"] + render_checks(result["_checks"])
    if result["left_out"]:
        lines += ["", "Features left out (the build still passes; upstream's text runs in their place):"]
        lines += [f"  {k}: {v}" for k, v in sorted(result["left_out"].items())]
    if result["status"] == "ready":
        lines += ["", f"RESULT: {short(result['candidate'])} passes every strict rule and check.",
                  f"Adopt locally, review the diff, then sign-commit:", f"  {result['adopt_command']}"]
    else:
        lines += ["", f"RESULT: {short(result['candidate'])} fails strict check(s): {', '.join(result['failed_strict'])}.",
                  "The pin stays where it is. Update the rules in "
                  f"upstream/{fork.name}.json, then re-run `{LAUNCHER} check {fork.name}`."]
    return lines


def _public(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def cmd_check(ctx: Context, args) -> int:
    fork = ctx.fork(args.name)
    if fork.mode != "rules":
        raise ConfigError(f"{fork.name} is a watch-mode fork; use `skill-sync watch {fork.name}`")
    with workdir() as work:
        result = evaluate(ctx, fork, args.to, work, ctx.verify_backend(fork, args))
        lines = check_report(fork, result)
    for line in lines:
        say(line)
    write_outputs(args, lines, _public(result))
    return {"current": EXIT_NOTHING_NEWER, "ready": EXIT_OK}.get(result["status"], EXIT_FAIL)


# --------------------------------------------------------------------- bump --

def set_pin(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    needle = f'"sha": "{old}"'
    if text.count(needle) != 1:
        raise SkillSyncError(f"{path}: expected exactly one {needle}; edit the pin by hand", EXIT_USAGE)
    tmp = path.with_name(f".{path.name}{TEMP_MARK}{secrets.token_hex(4)}")
    tmp.write_text(text.replace(needle, f'"sha": "{new}"'), encoding="utf-8")
    os.replace(tmp, path)


def cmd_bump(ctx: Context, args) -> int:
    fork = ctx.fork(args.name)
    if not SHA_RE.match(args.to):
        raise ConfigError("bump --to takes the full 40-character commit SHA that check reported")
    with workdir() as work:
        if fork.mode == "watch":
            result = watch_result(ctx, fork, args.to)
            if args.to == fork.upstream.sha:
                say(f"{fork.name} is already pinned at {args.to}")
                return EXIT_NOTHING_NEWER
            repo = ctx.repo(fork)
            if not repo.is_ancestor(fork.upstream.sha, args.to):
                say(f"{args.to} is not a descendant of the pin; pins only move forward")
                return EXIT_FAIL
            if fork.upstream.require_signed:
                owner, name = fork.upstream.github
                verified, reason = github_verified(owner, name, args.to)
                if not verified:
                    say(f"{args.to} is not signed (GitHub verification: {reason}); the pin stays at {fork.upstream.sha}")
                    return EXIT_FAIL
            set_pin(fork_path(ctx.root, fork.name), fork.upstream.sha, args.to)
            say(f"pinned {fork.name} at {args.to} ({len(result['commits'])} upstream commit(s) acknowledged).")
            say(f"Review `git diff upstream/{fork.name}.json`, then sign-commit it with your port.")
            return EXIT_OK
        result = evaluate(ctx, fork, args.to, work, ctx.verify_backend(fork, args))
        lines = check_report(fork, result)
        for line in lines:
            say(line)
        if result["status"] == "current":
            return EXIT_NOTHING_NEWER
        if result["status"] != "ready":
            say("\nbump: nothing changed.")
            return EXIT_FAIL
        build = result["_build"]
        outputs = engine.assemble(build, work / "out")
        publish(ctx, fork, work / "out", outputs, (fork_path(ctx.root, fork.name), fork.upstream.sha, args.to))
    _note_left_out(build)
    say(f"\nbump: pinned {fork.name} at {args.to} and regenerated {', '.join(outputs)}.")
    say(f"Review `git diff -- upstream/{fork.name}.json {' '.join(outputs)}`, then sign-commit.")
    return EXIT_OK


# -------------------------------------------------------------------- watch --

def watch_result(ctx: Context, fork: Fork, to: Optional[str]) -> dict:
    repo = ctx.repo(fork)
    pin = fork.upstream.sha
    files = [w.path for w in fork.watch_files]
    with repo.locked():
        tip = repo.sync(pin)
        head = repo.fetch_ref(to) if to else tip
        commits = repo.commits_between(pin, head, files) if head != pin else []
        diff = repo.diff(pin, head, files) if head != pin else ""
        present = {name for path in files for _, _, _, name in repo.ls_tree(head, path)}
    missing = [f for f in files if f not in present]
    return {"name": fork.name, "mode": "watch", "upstream": fork.upstream.web_url, "branch": fork.upstream.branch,
            "pin": pin, "head": head, "candidate": head if (commits or diff or missing) else None,
            "status": "changes" if (commits or diff or missing) else "unchanged",
            "commits": commits, "missing": missing, "diff": diff,
            "adopt_command": f"{LAUNCHER} bump {fork.name} --to {head}" if (commits or diff or missing) else None}


def watch_report(fork: Fork, result: dict, max_diff_lines: int) -> List[str]:
    lines = [f"skill-sync watch {fork.name}",
             f"  upstream   {result['upstream']} ({result['branch']})",
             f"  pin        {result['pin']}",
             f"  head       {result['head']}"]
    if fork.local:
        lines.append(f"  local port {fork.local} (hand-maintained; skill-sync never writes it)")
    lines += ["", "Tracked files:"]
    for w in fork.watch_files:
        touched = sum(1 for c in result["commits"] if w.path in c["files"])
        state = "MISSING at head" if w.path in result["missing"] else f"{touched} commit(s)" if touched else "unchanged"
        lines.append(f"  {state:<16} {w.path}" + (f"  ({w.note})" if w.note else ""))
    if result["status"] == "unchanged":
        lines += ["", "RESULT: no upstream changes to the tracked files since the pin."]
        return lines
    lines += ["", f"Commits since the pin ({len(result['commits'])}):"]
    for c in result["commits"]:
        lines.append(f"  {short(c['sha'])} {c['date']} {c['subject']}")
        lines.extend(f"      {f}" for f in c["files"])
    diff = result["diff"].splitlines()
    lines += ["", f"Diff {short(result['pin'])}..{short(result['head'])} ({len(diff)} lines):"]
    lines += diff[:max_diff_lines]
    if len(diff) > max_diff_lines:
        lines.append(f"... {len(diff) - max_diff_lines} more lines; run `{LAUNCHER} watch {fork.name}` locally for all of it")
    lines += ["", "RESULT: upstream changed. Port what matters by hand, then acknowledge it:",
              f"  {result['adopt_command']}"]
    return lines


def cmd_watch(ctx: Context, args) -> int:
    fork = ctx.fork(args.name, "watch")
    result = watch_result(ctx, fork, args.to)
    lines = watch_report(fork, result, args.max_diff_lines)
    for line in lines:
        say(line)
    write_outputs(args, lines, {k: v for k, v in result.items() if k != "diff"})
    return EXIT_OK if result["status"] == "unchanged" else EXIT_FAIL


# ------------------------------------------------------------------- verify --

def _walk(base: Path) -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    if not base.exists():
        return found
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_NAMES)
        for name in sorted(filenames):
            if name in IGNORED_NAMES:
                continue
            full = Path(dirpath) / name
            found[full.relative_to(base).as_posix()] = full
        for d in dirnames:
            if (Path(dirpath) / d).is_symlink():
                found[(Path(dirpath) / d).relative_to(base).as_posix()] = Path(dirpath) / d
    return found


def compare(expected: Path, actual: Path, label: str) -> List[str]:
    problems: List[str] = []
    if actual.is_symlink() or not actual.is_dir():
        return [f"{label}: missing (expected a generated folder)"]
    want, have = _walk(expected), _walk(actual)
    for name in sorted(want.keys() - have.keys()):
        problems.append(f"{label}/{name}: missing")
    for name in sorted(have.keys() - want.keys()):
        problems.append(f"{label}/{name}: not generated by the rules (hand-added?)")
    for name in sorted(want.keys() & have.keys()):
        a, b = want[name], have[name]
        if b.is_symlink():
            problems.append(f"{label}/{name}: is a symlink")
            continue
        if a.read_bytes() != b.read_bytes():
            problems.append(f"{label}/{name}: content differs from the generated file (hand-edited?)")
            try:
                diff = difflib.unified_diff(a.read_text("utf-8").splitlines(), b.read_text("utf-8").splitlines(),
                                            f"generated/{name}", f"committed/{name}", lineterm="", n=1)
                problems.extend("    " + line for line in list(diff)[:30])
            except UnicodeDecodeError:
                pass
        elif (a.stat().st_mode & 0o111 != 0) != (b.stat().st_mode & 0o111 != 0):
            problems.append(f"{label}/{name}: executable bit differs")
    strays = sorted(actual.parent.glob(f".{actual.name}{TEMP_MARK}*"))
    parent = Path(label).parent.as_posix()
    problems.extend(f"{parent}/{p.name}: leftover from an interrupted sync; delete it" for p in strays)
    return problems


def cmd_verify(ctx: Context, args) -> int:
    names = args.names or [n for n in list_forks(ctx.root) if ctx.fork(n).mode == "rules"]
    summary = []
    ok = True
    if _journal(ctx.root).exists():
        ok = False
        say(f"FAIL upstream/{JOURNAL}: an update was interrupted; run `{LAUNCHER} sync <name>` to recover it")
    for name in names:
        fork = ctx.fork(name, "rules")
        backend = ctx.verify_backend(fork, args) if args.checks else None
        with workdir() as work:
            build = _build_at_pin(ctx, fork, work)
            checks = list(build.checks)
            if backend and build.ok:
                checks += ctx.run_verify(build, work, backend)
            failed = [c for c in checks if c.status == "fail" and c.strict]
            problems: List[str] = []
            if failed:
                problems.append(f"the pin no longer builds: {', '.join(c.id for c in failed)}")
                problems += render_checks(failed)
            else:
                engine.assemble(build, work / "out")
                for mapping in fork.paths:
                    problems += compare(work / "out" / mapping.output, ctx.root / mapping.output, mapping.output)
        status = "ok" if not problems else "differs"
        ok = ok and not problems
        summary.append({"name": name, "status": status, "problems": problems})
        say(f"{'OK  ' if not problems else 'FAIL'} {name} at {short(fork.upstream.sha)}: "
            + (", ".join(p.output for p in fork.paths) + " match the pin and rules" if not problems else
               f"{len([p for p in problems if not p.startswith('    ')])} problem(s)"))
        for problem in problems:
            say(f"     {problem}")
    if not ok:
        say(f"\nRegenerate with `{LAUNCHER} sync <name>`; change upstream/<name>.json rather than the generated files.")
    if args.json:
        Path(args.json).write_text(json.dumps({"ok": ok, "forks": summary}, indent=2) + "\n", encoding="utf-8")
    return EXIT_OK if ok else EXIT_FAIL


# --------------------------------------------------------------------- main --

UNSANDBOXED_FLAG = ("--unsandboxed-verify",)
UNSANDBOXED_HELP = {"action": "store_true",
                    "help": "run verify commands (upstream code) without a sandbox; only for disposable CI runners with no secrets"}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skill-sync", description=__doc__,
                                epilog="Exit codes: 0 ok/ready/unchanged, 1 fails/differs/changed, "
                                       "2 usage, 3 nothing newer, 4 git/network/filesystem error.")
    p.add_argument("--root", help="repository root (default: the checkout this tool lives in)")
    p.add_argument("--cache", help="upstream cache outside the repo (default: $SKILL_SYNC_CACHE or ~/.cache/skill-sync)")
    sub = p.add_subparsers(dest="command", metavar="command")
    sub.required = True
    sub.add_parser("list", help="list fork definitions")
    s = sub.add_parser("sync", help="regenerate skills/<name>/ at the pinned SHA")
    s.add_argument("name")
    s.add_argument("--checks", action="store_true", help="also run the fork's verify commands before writing")
    s.add_argument(*UNSANDBOXED_FLAG, **UNSANDBOXED_HELP)
    s = sub.add_parser("check", help="evaluate the newest upstream commit (or --to REF); writes nothing")
    s.add_argument("name")
    s.add_argument("--to", metavar="REF", help="a commit SHA, branch or tag instead of the newest commit")
    s.add_argument("--json", metavar="FILE", help="also write the result as JSON")
    s.add_argument("--report", metavar="FILE", help="also write the report as text")
    s.add_argument(*UNSANDBOXED_FLAG, **UNSANDBOXED_HELP)
    s = sub.add_parser("bump", help="check SHA, then move the pin and regenerate the output locally")
    s.add_argument("name")
    s.add_argument("--to", metavar="SHA", required=True)
    s.add_argument(*UNSANDBOXED_FLAG, **UNSANDBOXED_HELP)
    s = sub.add_parser("watch", help="report upstream changes to a watch-mode fork's files")
    s.add_argument("name")
    s.add_argument("--to", metavar="REF")
    s.add_argument("--max-diff-lines", type=int, default=1500)
    s.add_argument("--json", metavar="FILE")
    s.add_argument("--report", metavar="FILE")
    s = sub.add_parser("verify", help="confirm committed outputs match their pins and rules byte for byte")
    s.add_argument("names", nargs="*", metavar="name")
    s.add_argument("--checks", action="store_true", help="also run each fork's verify commands")
    s.add_argument(*UNSANDBOXED_FLAG, **UNSANDBOXED_HELP)
    s.add_argument("--json", metavar="FILE")
    return p


def _terminate(signum, frame):
    raise KeyboardInterrupt


COMMANDS = {"list": cmd_list, "sync": cmd_sync, "check": cmd_check, "bump": cmd_bump,
            "watch": cmd_watch, "verify": cmd_verify}


def main(argv: Optional[List[str]] = None) -> int:
    args = parser().parse_args(argv)
    # A cancelled CI job sends SIGTERM: unwind like Ctrl-C, so verify's process group is killed.
    signal.signal(signal.SIGTERM, _terminate)
    root = Path(args.root).resolve() if args.root else TOOL_ROOT.parents[1]
    ctx = Context(root, args.cache)
    try:
        if args.command in ("sync", "bump"):
            # Before any fork is loaded: an interrupted update may have left a half-written pin.
            with ctx.repo_lock():
                note = recover(ctx.root)
            if note:
                say(f"note: {note}")
        return COMMANDS[args.command](ctx, args)
    except SkillSyncError as exc:
        print(f"skill-sync: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print("skill-sync: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Output piped into something that stopped reading (`| head`): exit quietly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1
