"""Builds a fork's output from one upstream commit, entirely inside a work directory.

Order: copy the declared upstream paths (plus support files and the license) into
a stage that mirrors upstream's layout -> file deletions -> rules, in order ->
license copy -> tripwire -> link check. Verify commands run on a copy of that
stage, so nothing they write can reach the output.

A strict rule that fails makes the whole build fail. A feature rule that fails
leaves its feature out (plus every feature that requires it): the stage is
rebuilt from scratch without those features' rules, so a left-out feature never
leaves half its edits behind.
"""
from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import sandbox
from .config import Fork, Rule, SkillSyncError, matches, upstream_relative_to_output
from .gitcache import Repo

UPSTREAM_FILE = "UPSTREAM.md"
OUTPUT_LICENSE = "LICENSE"


@dataclass
class Check:
    id: str
    kind: str          # path | delete | replace | remove-section | license | tripwire | links | verify | signature
    status: str        # pass | fail | left-out | skipped
    strict: bool = True
    feature: Optional[str] = None
    message: str = ""
    reason: str = ""
    files: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class Build:
    fork: Fork
    sha: str
    stage: Path
    checks: List[Check]
    left_out: Dict[str, str]           # feature -> why
    applied: List[Rule]

    @property
    def strict_failures(self) -> List[Check]:
        return [c for c in self.checks if c.status == "fail" and c.strict]

    @property
    def ok(self) -> bool:
        return not self.strict_failures


class Drift(Exception):
    """A rule's expectation doesn't hold for this upstream commit."""

    def __init__(self, message: str, files: Optional[List[str]] = None):
        super().__init__(message)
        self.files = files or []


# ------------------------------------------------------------------ staging --

def _populate(repo: Repo, fork: Fork, sha: str, stage: Path) -> List[Check]:
    checks: List[Check] = []
    wanted: List[Tuple[str, str]] = [(p.upstream, "path") for p in fork.paths]
    wanted += [(s, "support") for s in fork.support]
    if fork.license:
        wanted.append((fork.license, "license"))
    entries: Dict[str, Tuple[str, str]] = {}
    for path, role in wanted:
        found = repo.ls_tree(sha, path)
        if not found:
            checks.append(Check(f"{role}:{path}", "path", "fail", message=f"{path} does not exist upstream at {sha[:12]}"))
            continue
        bad = [(m, n) for m, t, _, n in found if t != "blob" or m not in ("100644", "100755")]
        if bad:
            kinds = {"120000": "symlink", "160000": "submodule"}
            listed = ", ".join(f"{n} ({kinds.get(m, m)})" for m, n in bad[:10])
            checks.append(Check(f"{role}:{path}", "path", "fail", message=f"unsupported entries: {listed}",
                                files=[n for _, n in bad]))
            continue
        for mode, _, oid, name in found:
            entries[name] = (mode, oid)
        checks.append(Check(f"{role}:{path}", "path", "pass", message=f"{len(found)} file(s)"))
    blobs = repo.read_blobs([oid for _, oid in entries.values()])
    for name, (mode, oid) in sorted(entries.items()):
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blobs[oid])
        os.chmod(target, 0o755 if mode == "100755" else 0o644)
    return checks


def stage_files(stage: Path) -> List[str]:
    files = []
    for dirpath, dirnames, filenames in os.walk(stage):
        dirnames.sort()
        for name in sorted(filenames):
            full = Path(dirpath) / name
            files.append(full.relative_to(stage).as_posix())
    return files


# -------------------------------------------------------------------- rules --

def _targets(stage: Path, rule: Rule) -> List[str]:
    return [f for f in stage_files(stage) if matches(rule.file, f)]


def _apply_delete(stage: Path, rule: Rule) -> List[str]:
    found = _targets(stage, rule)
    if len(found) != rule.count:
        raise Drift(f"expected {rule.count} file(s) matching {rule.file}, found {len(found)}", found)
    for name in found:
        (stage / name).unlink()
    _prune_empty_dirs(stage)
    return found


def _prune_empty_dirs(stage: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(stage, topdown=False):
        if Path(dirpath) != stage and not os.listdir(dirpath):
            os.rmdir(dirpath)


def _read_text(stage: Path, name: str) -> str:
    try:
        return (stage / name).read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise Drift(f"{name} is not UTF-8 text", [name]) from None


def _write_text(stage: Path, name: str, text: str) -> None:
    path = stage / name
    mode = path.stat().st_mode
    path.write_bytes(text.encode("utf-8"))
    os.chmod(path, mode)


def _apply_replace(stage: Path, rule: Rule) -> List[str]:
    found = _targets(stage, rule)
    if not found:
        raise Drift(f"no file matches {rule.file}")
    counts = {name: _read_text(stage, name).count(rule.anchor) for name in found}
    total = sum(counts.values())
    if total != rule.count:
        where = ", ".join(f"{n}: {c}" for n, c in counts.items() if c) or "no file"
        raise Drift(f"expected {rule.count} match(es) of the anchor in {rule.file}, found {total} ({where}); "
                    f"anchor starts {_preview(rule.anchor)}", found)
    touched = [n for n, c in counts.items() if c]
    for name in touched:
        _write_text(stage, name, _read_text(stage, name).replace(rule.anchor, rule.replacement or ""))
    return touched


FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")


def parse_heading(line: str) -> Optional[Tuple[int, str]]:
    """(level, title) for an ATX heading (CommonMark: up to 3 spaces of indent), else None."""
    match = HEADING_RE.match(line)
    if not match:
        return None
    title = match.group(2) or ""
    # An optional closing sequence: spaces, then only #s.
    title = re.sub(r"(?:^|[ \t]+)#+$", "", title).strip()
    return len(match.group(1)), title


def markdown_lines(text: str):
    """(index, line, in_code) for each line, tracking fenced code blocks (CommonMark rules).

    A fence closes only on a line of the same character, at least as long as the
    opening fence, with nothing after it but spaces.
    """
    fence: Optional[Tuple[str, int]] = None
    for index, line in enumerate(text.split("\n")):
        if fence is None:
            match = FENCE_OPEN_RE.match(line)
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                fence = (match.group(1)[0], len(match.group(1)))
                yield index, line, True
                continue
            yield index, line, False
            continue
        char, length = fence
        if re.match(r"^ {0,3}" + re.escape(char) + "{" + str(length) + r",}[ \t]*$", line):
            fence = None
        yield index, line, True


def _remove_sections(text: str, heading: str) -> Tuple[str, int]:
    """Removes each section headed `heading`, up to the next heading of the same or a higher level."""
    target = parse_heading(heading)
    assert target is not None
    level = target[0]
    lines = text.split("\n")
    flags = list(markdown_lines(text))
    drop: Set[int] = set()
    count = 0
    i = 0
    while i < len(flags):
        _, line, code = flags[i]
        if not code and parse_heading(line) == target:
            count += 1
            j = i + 1
            while j < len(flags):
                _, other, other_code = flags[j]
                parsed = None if other_code else parse_heading(other)
                if parsed and parsed[0] <= level:
                    break
                j += 1
            drop.update(range(i, j))
            i = j
            continue
        i += 1
    kept = [line for index, line in enumerate(lines) if index not in drop]
    result = "\n".join(kept)
    if count and text.endswith("\n"):
        result = result.rstrip("\n") + "\n"
    return result, count


def _apply_remove_section(stage: Path, rule: Rule) -> List[str]:
    found = [f for f in _targets(stage, rule)]
    if not found:
        raise Drift(f"no file matches {rule.file}")
    results = {name: _remove_sections(_read_text(stage, name), rule.anchor) for name in found}
    total = sum(count for _, count in results.values())
    if total != rule.count:
        raise Drift(f"expected {rule.count} section(s) headed {rule.anchor!r} in {rule.file}, found {total}", found)
    touched = [n for n, (_, c) in results.items() if c]
    for name in touched:
        _write_text(stage, name, results[name][0])
    return touched


APPLY = {"delete": _apply_delete, "replace": _apply_replace, "remove-section": _apply_remove_section}


def _preview(text: str, limit: int = 60) -> str:
    first = text.split("\n", 1)[0]
    return repr(first[:limit] + ("..." if len(first) > limit or "\n" in text else ""))


# ----------------------------------------------------------------- features --

def _closure(fork: Fork, failed: Dict[str, str]) -> Dict[str, str]:
    left = dict(failed)
    changed = True
    while changed:
        changed = False
        for feature in fork.features.values():
            if feature.name in left:
                continue
            missing = [r for r in feature.requires if r in left]
            if missing:
                left[feature.name] = f"requires left-out feature {missing[0]}"
                changed = True
    return left


# -------------------------------------------------------------------- build --

def build(repo: Repo, fork: Fork, sha: str, work: Path) -> Build:
    repo.verify_sha(sha)
    left_out: Dict[str, str] = {}
    failures_seen: Dict[str, Check] = {}
    for attempt in range(len(fork.features) + 2):
        stage = work / f"stage-{attempt}"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        checks = _populate(repo, fork, sha, stage)
        applied: List[Rule] = []
        newly: Dict[str, str] = {}
        for rule in fork.deletions + fork.rules:
            if rule.feature and rule.feature in left_out:
                prior = failures_seen.get(rule.id)
                checks.append(Check(rule.id, rule.kind, "left-out", False, rule.feature,
                                    prior.message if prior else f"feature {rule.feature} left out: {left_out[rule.feature]}",
                                    rule.reason, prior.files if prior else []))
                continue
            try:
                touched = APPLY[rule.kind](stage, rule)
            except Drift as drift:
                check = Check(rule.id, rule.kind, "fail", rule.strict, rule.feature, str(drift), rule.reason, drift.files)
                checks.append(check)
                if rule.feature:
                    failures_seen[rule.id] = check
                    newly.setdefault(rule.feature, f"rule {rule.id} no longer applies")
                continue
            applied.append(rule)
            checks.append(Check(rule.id, rule.kind, "pass", rule.strict, rule.feature,
                                _applied_message(rule, touched), rule.reason, touched))
        if newly:
            left_out = _closure(fork, {**left_out, **newly})
            shutil.rmtree(stage)
            continue
        break
    else:  # pragma: no cover - each pass leaves out at least one more feature
        raise SkillSyncError("feature resolution did not settle")

    checks.append(_copy_license(fork, stage))
    if fork.tripwire:
        checks.append(tripwire(fork, stage))
    if fork.links:
        checks.append(check_links(fork, stage))
    return Build(fork, sha, stage, checks, left_out, applied)


def _applied_message(rule: Rule, touched: List[str]) -> str:
    if rule.kind == "delete":
        return "deleted " + ", ".join(touched)
    unit = "section(s)" if rule.kind == "remove-section" else "match(es)"
    return f"{rule.count} {unit} in {', '.join(touched) or '(none expected)'}"


def _copy_license(fork: Fork, stage: Path) -> Check:
    if not fork.license:
        return Check("license", "license", "pass", message="no license file declared")
    source = stage / fork.license
    if not source.is_file():
        return Check("license", "license", "fail", message=f"{fork.license} is missing")
    data = source.read_bytes()
    for mapping in fork.paths:
        target = stage / mapping.upstream / OUTPUT_LICENSE
        if target.exists() and target.read_bytes() != data:
            return Check("license", "license", "fail",
                         message=f"{mapping.upstream} ships its own {OUTPUT_LICENSE}, which differs from {fork.license}")
        if (stage / mapping.upstream).is_dir():
            target.write_bytes(data)
            os.chmod(target, 0o644)
    return Check("license", "license", "pass", message=f"{fork.license} copied into each output as {OUTPUT_LICENSE}")


def output_files(fork: Fork, stage: Path) -> List[str]:
    return [f for f in stage_files(stage) if upstream_relative_to_output(fork, f)]


# ----------------------------------------------------------------- tripwire --

def tripwire(fork: Fork, stage: Path) -> Check:
    tw = fork.tripwire
    assert tw is not None
    flags = re.IGNORECASE if tw.ignore_case else 0
    pattern = re.compile("|".join(re.escape(t) for t in tw.terms), flags)
    hits: List[str] = []
    used = set()
    for name in output_files(fork, stage):
        whole = [i for i, a in enumerate(tw.allow) if a.literal is None and any(matches(g, name) for g in a.paths)]
        if whole:
            used.update(whole)
            continue
        text = (stage / name).read_bytes().decode("utf-8", "replace")
        for i, allow in enumerate(tw.allow):
            if allow.literal and any(matches(g, name) for g in allow.paths) and allow.literal in text:
                used.add(i)
                text = text.replace(allow.literal, " " * len(allow.literal))
        for number, line in enumerate(text.split("\n"), 1):
            for match in pattern.finditer(line):
                hits.append(f"{name}:{number}: {match.group(0)!r} in {line.strip()[:120]!r}")
    unused = [a for i, a in enumerate(tw.allow) if i not in used]
    note = ""
    if unused:
        note = "; unused allowlist entries: " + ", ".join(
            (a.literal and repr(a.literal) or "whole file") + " in " + "/".join(a.paths) for a in unused)
    if hits:
        shown = "\n".join(hits[:40]) + ("\n..." if len(hits) > 40 else "")
        return Check("tripwire", "tripwire", "fail", message=f"{len(hits)} hit(s) for {', '.join(tw.terms)}:\n{shown}{note}",
                     files=sorted({h.split(':', 1)[0] for h in hits}))
    return Check("tripwire", "tripwire", "pass", message=f"no {', '.join(tw.terms)} outside the allowlist{note}")


# -------------------------------------------------------------------- links --

LINK_RE = re.compile(r"(?<!\!)\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")


def heading_slugs(text: str) -> Set[str]:
    slugs: Dict[str, int] = {}
    result = set()
    for _, line, code in markdown_lines(text):
        parsed = None if code else parse_heading(line)
        if not parsed:
            continue
        title = parsed[1]
        title = re.sub(r"`([^`]*)`", r"\1", title)
        title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
        slug = "".join(ch for ch in unicodedata.normalize("NFC", title.lower())
                       if ch.isalnum() or ch in " -_").replace(" ", "-")
        n = slugs.get(slug, 0)
        slugs[slug] = n + 1
        result.add(slug if n == 0 else f"{slug}-{n}")
    return result


def check_links(fork: Fork, stage: Path) -> Check:
    """Every relative Markdown link in an output resolves inside that output (heading anchors too)."""
    problems: List[str] = []
    files = set(output_files(fork, stage))
    for name in sorted(files):
        if not name.endswith(".md"):
            continue
        text = (stage / name).read_bytes().decode("utf-8", "replace")
        mapping, _ = upstream_relative_to_output(fork, name)
        for number, (_, line, code) in enumerate(markdown_lines(text), 1):
            if code:
                continue
            for match in LINK_RE.finditer(INLINE_CODE_RE.sub("", line)):
                target = match.group(1)
                if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                    continue
                path_part, _, anchor = target.partition("#")
                resolved = name if not path_part else os.path.normpath(os.path.join(os.path.dirname(name), path_part)).replace(os.sep, "/")
                if not (resolved == mapping.upstream or resolved.startswith(mapping.upstream + "/")):
                    problems.append(f"{name}:{number}: {target} leaves the vendored folder")
                    continue
                if resolved not in files and not (stage / resolved).is_dir():
                    problems.append(f"{name}:{number}: {target} does not exist")
                    continue
                if anchor and resolved.endswith(".md"):
                    slugs = heading_slugs((stage / resolved).read_bytes().decode("utf-8", "replace"))
                    if anchor.lower() not in slugs:
                        problems.append(f"{name}:{number}: {target} names no heading in {resolved}")
    if problems:
        return Check("links", "links", "fail", message=f"{len(problems)} broken link(s):\n" + "\n".join(problems[:40]),
                     files=sorted({p.split(':', 1)[0] for p in problems}))
    return Check("links", "links", "pass", message="every relative link resolves inside its folder")


# ------------------------------------------------------------------- verify --

def find_python(minimum: Tuple[int, int]) -> str:
    """The interpreter for `{python}`: this one if new enough, else the newest python3.N on PATH.

    Deterministic for a given machine: the running interpreter, then python3.30 down to
    the minimum, then plain python3; the first that reports a version >= minimum wins.
    """
    candidates = [sys.executable]
    candidates += [sandbox.which(f"python3.{minor}") for minor in range(30, minimum[1] - 1, -1)]
    candidates.append(sandbox.which("python3"))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            out = subprocess.run([candidate, "-I", "-c", "import sys; print(*sys.version_info[:2])"],
                                 capture_output=True, text=True, timeout=20).stdout.split()
        except (OSError, subprocess.TimeoutExpired):
            continue
        if len(out) == 2 and (int(out[0]), int(out[1])) >= minimum:
            return candidate
    raise SkillSyncError(f"verify needs Python {minimum[0]}.{minimum[1]}+ for {{python}}; none found on PATH "
                         "(install one, e.g. `uv python install`, and put it on PATH)", 4)


def run_verify(build_result: Build, work: Path, backend: str, forbidden: List[str],
               log_dir: Optional[Path] = None) -> List[Check]:
    """Runs each verify command in a sandboxed copy of the stage. Upstream code runs here."""
    fork = build_result.fork
    checks: List[Check] = []
    if not fork.verify:
        return checks
    root = work / "verify-root"
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(build_result.stage, root, symlinks=False)
    python = find_python(fork.verify_python)
    box = sandbox.prepare(backend, work, root, [python], forbidden, {}, fork.verify_shared_tmp)
    failed = False
    for command in fork.verify:
        check_id = f"verify:{command.name}"
        if failed:
            checks.append(Check(check_id, "verify", "skipped", message="an earlier verify command failed"))
            continue
        argv = [a.replace("{python}", python) for a in command.argv]
        log = work / f"verify-{command.name}.log"
        command_box = dataclasses.replace(box, env={**box.env, **command.env})
        code, elapsed = sandbox.run(command_box, argv, log, command.timeout)
        if code == 0:
            checks.append(Check(check_id, "verify", "pass", message=f"exit 0 in {elapsed:.0f}s ({backend})"))
            continue
        failed = True
        tail = log.read_bytes().decode("utf-8", "replace").splitlines()[-40:]
        status = "timed out; its process group was killed" if code is None else f"exit {code}"
        kept = ""
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)
            saved = log_dir / f"{fork.name}-{command.name}-{build_result.sha[:12]}-{time.strftime('%Y%m%dT%H%M%S')}.log"
            shutil.copyfile(log, saved)
            kept = f"\nfull log: {saved}"
        checks.append(Check(check_id, "verify", "fail",
                            message=f"{status} after {elapsed:.0f}s ({backend}): {' '.join(command.argv)}\n"
                                    + "\n".join(tail) + kept))
    return checks


# ------------------------------------------------------------------ outputs --

def assemble(build_result: Build, out_root: Path) -> List[str]:
    """Writes each output folder (with LICENSE and UPSTREAM.md) under out_root; returns output paths."""
    fork = build_result.fork
    for mapping in fork.paths:
        source = build_result.stage / mapping.upstream
        target = out_root / mapping.output
        if not source.is_dir():
            raise SkillSyncError(f"{mapping.upstream} is not a folder at {build_result.sha[:12]}")
        if (source / UPSTREAM_FILE).exists():
            raise SkillSyncError(f"{mapping.upstream} already ships an {UPSTREAM_FILE}; skill-sync can't add its own")
        shutil.copytree(source, target, symlinks=False)
        (target / UPSTREAM_FILE).write_bytes(provenance(build_result, mapping).encode("utf-8"))
        os.chmod(target / UPSTREAM_FILE, 0o644)
    return [m.output for m in fork.paths]


def _in_mapping(name: str, mapping) -> bool:
    return name == mapping.upstream or name.startswith(mapping.upstream + "/")


def _inside(mapping, name: str) -> str:
    return name[len(mapping.upstream) + 1:] if name.startswith(mapping.upstream + "/") else name


def provenance(build_result: Build, mapping) -> str:
    fork, sha = build_result.fork, build_result.sha
    up = fork.upstream
    lines = [
        "# Upstream provenance",
        "",
        "> Generated by `tooling/skill-sync` from `upstream/%s.json`. Do not hand-edit" % fork.name,
        "> anything in this folder: `skill-sync verify` fails when it differs from what",
        "> the pin and rules produce. Change the fork definition, then run",
        "> `tooling/skill-sync/skill-sync sync %s`." % fork.name,
        "",
        f"- Upstream: {up.web_url}",
        f"- Branch: `{up.branch}`",
        f"- Path: `{mapping.upstream}` (vendored as `{mapping.output}`)",
        f"- Commit: `{sha}`",
    ]
    if fork.license:
        lines.append(f"- License: upstream `{fork.license}`, copied here as `{OUTPUT_LICENSE}`")
    deletions = [r for r in build_result.applied if r.kind == "delete"]
    rules = [r for r in build_result.applied if r.kind != "delete"]

    def touched(rule: Rule) -> List[str]:
        check = next(c for c in build_result.checks if c.id == rule.id)
        return [f for f in check.files if _in_mapping(f, mapping)]

    lines += ["", "## Files removed", ""]
    entries = [(r, touched(r)) for r in deletions]
    entries = [(r, files) for r, files in entries if files]
    lines += [f"- `{', '.join(_inside(mapping, f) for f in files)}` (`{r.id}`): {r.reason}" for r, files in entries] or ["None."]

    lines += ["", "## Rules applied", ""]
    entries = [(r, touched(r)) for r in rules]
    entries = [(r, files) for r, files in entries if files]
    for rule, files in entries:
        kind = "strict" if rule.strict else f"feature `{rule.feature}`"
        lines.append(f"- `{rule.id}` ({kind}, {rule.kind} in `{', '.join(_inside(mapping, f) for f in files)}`): {rule.reason}")
    if not entries:
        lines.append("None.")

    lines += ["", "## Features left out", ""]
    if build_result.left_out:
        for name, why in sorted(build_result.left_out.items()):
            lines.append(f"- `{name}`: {why}. Upstream's text runs in its place.")
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"
