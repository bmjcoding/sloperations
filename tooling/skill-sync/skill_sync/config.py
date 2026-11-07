"""Fork definitions: `upstream/<name>.json`, loaded and validated strictly.

Every path in a definition is relative to the upstream repository root, except
`paths[].output`, which is relative to this repository's root. Unknown keys are
errors, so a typo never silently disables a rule.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]*$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
GITHUB_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")

# Exit codes shared by every command.
EXIT_OK = 0
EXIT_FAIL = 1          # candidate fails / output differs / upstream changed (watch)
EXIT_USAGE = 2         # bad arguments or fork definition
EXIT_NOTHING_NEWER = 3  # check/bump: the pin is already the newest candidate
EXIT_ERROR = 4         # git, network or filesystem trouble; nothing was decided


class SkillSyncError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR):
        super().__init__(message)
        self.code = code


class ConfigError(SkillSyncError):
    def __init__(self, message: str):
        super().__init__(message, EXIT_USAGE)


@dataclass
class Upstream:
    repo: str
    branch: str
    sha: str
    require_signed: bool
    filter: Optional[str]
    github: Optional[Tuple[str, str]]

    @property
    def web_url(self) -> str:
        if self.github:
            return f"https://github.com/{self.github[0]}/{self.github[1]}"
        return self.repo


@dataclass
class PathMap:
    upstream: str
    output: str


@dataclass
class Rule:
    id: str
    kind: str              # "replace" | "remove-section" | "delete"
    file: str              # glob, upstream-relative
    anchor: Optional[str]
    replacement: Optional[str]
    count: int
    feature: Optional[str]  # None means strict
    reason: str

    @property
    def strict(self) -> bool:
        return self.feature is None


@dataclass
class Feature:
    name: str
    description: str
    requires: List[str]


@dataclass
class Allow:
    paths: List[str]
    literal: Optional[str]
    reason: str


@dataclass
class Tripwire:
    terms: List[str]
    ignore_case: bool
    allow: List[Allow]


@dataclass
class VerifyCommand:
    name: str
    argv: List[str]
    timeout: int
    env: Dict[str, str]


@dataclass
class WatchFile:
    path: str
    note: str


@dataclass
class Fork:
    name: str
    mode: str  # "rules" | "watch"
    description: str
    upstream: Upstream
    source: Path
    # The exact definition text this fork was parsed from: publish refuses to write
    # outputs built from a definition that has since changed.
    source_text: str = ""
    paths: List[PathMap] = field(default_factory=list)
    license: Optional[str] = None
    support: List[str] = field(default_factory=list)
    features: Dict[str, Feature] = field(default_factory=dict)
    deletions: List[Rule] = field(default_factory=list)
    rules: List[Rule] = field(default_factory=list)
    tripwire: Optional[Tripwire] = None
    links: bool = False
    verify: List[VerifyCommand] = field(default_factory=list)
    verify_python: Tuple[int, int] = (3, 9)
    verify_shared_tmp: List[str] = field(default_factory=list)
    watch_files: List[WatchFile] = field(default_factory=list)
    local: Optional[str] = None

    def tracked_paths(self) -> List[str]:
        """Upstream paths whose changes make a new candidate."""
        if self.mode == "watch":
            return [w.path for w in self.watch_files]
        tracked = [p.upstream for p in self.paths] + list(self.support)
        if self.license:
            tracked.append(self.license)
        return tracked


# ------------------------------------------------------------------ helpers --

def _keys(obj: Any, where: str, required: set, optional: set) -> dict:
    if not isinstance(obj, dict):
        raise ConfigError(f"{where}: expected an object")
    missing = required - obj.keys()
    if missing:
        raise ConfigError(f"{where}: missing {', '.join(sorted(missing))}")
    unknown = obj.keys() - required - optional
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    return obj


def _str(value: Any, where: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ConfigError(f"{where}: expected a non-empty string")
    return value


def _text(value: Any, where: str) -> str:
    """A string, or a list of lines joined with "\\n" (end with "" for a trailing newline)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return "\n".join(value)
    raise ConfigError(f"{where}: expected a string or a list of lines")


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: expected true or false")
    return value


def _int(value: Any, where: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ConfigError(f"{where}: expected an integer >= {minimum}")
    return value


def rel_path(value: Any, where: str, glob: bool = False) -> str:
    """A normalized relative POSIX path that stays inside its root."""
    text = _str(value, where)
    if "\\" in text or text.startswith("/") or "\x00" in text:
        raise ConfigError(f"{where}: {text!r} must be a relative POSIX path")
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ConfigError(f"{where}: {text!r} must be normalized (no empty, '.' or '..' parts)")
    if not glob and any(ch in text for ch in "*?["):
        raise ConfigError(f"{where}: {text!r} must be a literal path, not a glob")
    return text


def glob_regex(pattern: str) -> "re.Pattern[str]":
    """`*` and `?` stay within one path segment; a `**` segment spans any depth."""
    parts = pattern.split("/")
    out = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            out.append(".+" if last else "(?:[^/]+/)*")
            continue
        segment = "".join("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in part)
        out.append(segment if last else segment + "/")
    return re.compile("^" + "".join(out) + "$")


def matches(pattern: str, path: str) -> bool:
    return bool(glob_regex(pattern).match(path))


# ------------------------------------------------------------------ loading --

def fork_path(root: Path, name: str) -> Path:
    if not NAME_RE.match(name):
        raise ConfigError(f"fork name {name!r} must match {NAME_RE.pattern}")
    return root / "upstream" / f"{name}.json"


def list_forks(root: Path) -> List[str]:
    directory = root / "upstream"
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if NAME_RE.match(p.stem))


def load_fork(root: Path, name: str) -> Fork:
    path = fork_path(root, name)
    if not path.is_file():
        raise ConfigError(f"no fork definition at {path}")
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicates)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON: {exc}") from None
    fork = parse_fork(data, path, name)
    fork.source_text = text
    return fork


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"duplicate key {key!r}")
        result[key] = value
    return result


def parse_fork(data: Any, source: Path, name: str) -> Fork:
    where = source.name
    common = {"name", "mode", "upstream"}
    mode = data.get("mode") if isinstance(data, dict) else None
    if mode == "watch":
        _keys(data, where, common, {"description", "files", "local", "$comment"})
    elif mode == "rules":
        _keys(data, where, common | {"paths"}, {
            "description", "license", "support", "features", "delete", "rules",
            "tripwire", "links", "verify", "verify_python", "verify_shared_tmp", "$comment"})
    else:
        raise ConfigError(f"{where}: mode must be \"rules\" or \"watch\"")
    if data["name"] != name:
        raise ConfigError(f"{where}: name {data['name']!r} must match the file name {name!r}")

    up = _keys(data["upstream"], f"{where}: upstream", {"repo", "branch", "sha"}, {"require_signed", "filter"})
    repo = _str(up["repo"], f"{where}: upstream.repo")
    github = GITHUB_RE.match(repo)
    branch = _str(up["branch"], f"{where}: upstream.branch")
    if not BRANCH_RE.match(branch) or branch.startswith("-") or ".." in branch:
        raise ConfigError(f"{where}: upstream.branch {branch!r} is not a plain branch name")
    sha = _str(up["sha"], f"{where}: upstream.sha")
    if not SHA_RE.match(sha):
        raise ConfigError(f"{where}: upstream.sha must be a full 40-character lowercase commit SHA")
    require_signed = _bool(up.get("require_signed", False), f"{where}: upstream.require_signed")
    if require_signed and not github:
        raise ConfigError(f"{where}: upstream.require_signed needs a https://github.com/<owner>/<repo> repo")
    filt = up.get("filter")
    if filt is not None and filt != "blob:none":
        raise ConfigError(f"{where}: upstream.filter may only be \"blob:none\"")
    upstream = Upstream(repo, branch, sha, require_signed, filt,
                        (github.group(1), github.group(2)) if github else None)

    fork = Fork(name=name, mode=mode, description=_str(data.get("description", ""), f"{where}: description", True),
                upstream=upstream, source=source)
    if mode == "watch":
        files = data.get("files")
        if not isinstance(files, list) or not files:
            raise ConfigError(f"{where}: files must be a non-empty list")
        for i, entry in enumerate(files):
            entry = _keys(entry, f"{where}: files[{i}]", {"path"}, {"note"})
            fork.watch_files.append(WatchFile(rel_path(entry["path"], f"{where}: files[{i}].path"),
                                              _str(entry.get("note", ""), f"{where}: files[{i}].note", True)))
        if "local" in data:
            fork.local = rel_path(data["local"], f"{where}: local")
        return fork

    paths = data["paths"]
    if not isinstance(paths, list) or not paths:
        raise ConfigError(f"{where}: paths must be a non-empty list")
    for i, entry in enumerate(paths):
        entry = _keys(entry, f"{where}: paths[{i}]", {"upstream", "output"}, set())
        fork.paths.append(PathMap(rel_path(entry["upstream"], f"{where}: paths[{i}].upstream"),
                                  rel_path(entry["output"], f"{where}: paths[{i}].output")))
    outputs = [p.output for p in fork.paths]
    for a in outputs:
        if a.split("/")[0] in ("upstream", "tooling", ".git", ".github"):
            raise ConfigError(f"{where}: output {a!r} would overwrite repository tooling")
        for b in outputs:
            if a != b and (b + "/").startswith(a + "/"):
                raise ConfigError(f"{where}: outputs {a!r} and {b!r} overlap")
    ups = [p.upstream for p in fork.paths]
    for a in ups:
        for b in ups:
            if a != b and (b + "/").startswith(a + "/"):
                raise ConfigError(f"{where}: upstream paths {a!r} and {b!r} overlap")
    if len(set(outputs)) != len(outputs) or len(set(ups)) != len(ups):
        raise ConfigError(f"{where}: paths must be distinct")
    if "license" in data:
        fork.license = rel_path(data["license"], f"{where}: license")
    for i, item in enumerate(data.get("support", [])):
        fork.support.append(rel_path(item, f"{where}: support[{i}]"))

    for fname, spec in (data.get("features") or {}).items():
        if not NAME_RE.match(fname):
            raise ConfigError(f"{where}: feature name {fname!r} must match {NAME_RE.pattern}")
        spec = _keys(spec, f"{where}: features.{fname}", {"description"}, {"requires"})
        requires = spec.get("requires", [])
        if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
            raise ConfigError(f"{where}: features.{fname}.requires must be a list of feature names")
        fork.features[fname] = Feature(fname, _str(spec["description"], f"{where}: features.{fname}.description"), requires)
    for feature in fork.features.values():
        for req in feature.requires:
            if req not in fork.features:
                raise ConfigError(f"{where}: feature {feature.name!r} requires unknown feature {req!r}")

    seen = set()
    for i, entry in enumerate(data.get("delete", [])):
        rule = _parse_rule(entry, f"{where}: delete[{i}]", fork, deletion=True)
        fork.deletions.append(rule)
    for i, entry in enumerate(data.get("rules", [])):
        fork.rules.append(_parse_rule(entry, f"{where}: rules[{i}]", fork, deletion=False))
    for rule in fork.deletions + fork.rules:
        if rule.id in seen:
            raise ConfigError(f"{where}: duplicate rule id {rule.id!r}")
        seen.add(rule.id)

    if "tripwire" in data:
        tw = _keys(data["tripwire"], f"{where}: tripwire", {"terms"}, {"ignore_case", "allow"})
        terms = tw["terms"]
        if not isinstance(terms, list) or not terms or not all(isinstance(t, str) and t for t in terms):
            raise ConfigError(f"{where}: tripwire.terms must be a non-empty list of strings")
        allow = []
        for i, entry in enumerate(tw.get("allow", [])):
            entry = _keys(entry, f"{where}: tripwire.allow[{i}]", {"paths", "reason"}, {"literal"})
            globs = entry["paths"]
            if not isinstance(globs, list) or not globs:
                raise ConfigError(f"{where}: tripwire.allow[{i}].paths must be a non-empty list")
            allow.append(Allow([rel_path(g, f"{where}: tripwire.allow[{i}].paths", glob=True) for g in globs],
                               _str(entry["literal"], f"{where}: tripwire.allow[{i}].literal") if "literal" in entry else None,
                               _str(entry["reason"], f"{where}: tripwire.allow[{i}].reason")))
        fork.tripwire = Tripwire(list(terms), _bool(tw.get("ignore_case", True), f"{where}: tripwire.ignore_case"), allow)
    fork.links = _bool(data.get("links", False), f"{where}: links")

    shared = data.get("verify_shared_tmp", [])
    if not isinstance(shared, list) or not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,}-", x)
                                               for x in shared):
        raise ConfigError(f"{where}: verify_shared_tmp must list name prefixes ending in '-', e.g. \"tool-scratch-\"")
    fork.verify_shared_tmp = list(shared)
    if "verify_python" in data:
        spec = _str(data["verify_python"], f"{where}: verify_python")
        match = re.fullmatch(r">=\s*3\.(\d+)", spec)
        if not match:
            raise ConfigError(f"{where}: verify_python must look like \">=3.10\"")
        fork.verify_python = (3, int(match.group(1)))
    for i, entry in enumerate(data.get("verify", [])):
        entry = _keys(entry, f"{where}: verify[{i}]", {"name", "argv"}, {"timeout", "env"})
        vname = _str(entry["name"], f"{where}: verify[{i}].name")
        if not ID_RE.match(vname):
            raise ConfigError(f"{where}: verify[{i}].name must match {ID_RE.pattern}")
        argv = entry["argv"]
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ConfigError(f"{where}: verify[{i}].argv must be a non-empty list of strings")
        env = entry.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise ConfigError(f"{where}: verify[{i}].env must map names to strings")
        fork.verify.append(VerifyCommand(vname, list(argv), _int(entry.get("timeout", 1800), f"{where}: verify[{i}].timeout", 1), env))
    return fork


def _parse_rule(entry: Any, where: str, fork: Fork, deletion: bool) -> Rule:
    if deletion:
        entry = _keys(entry, where, {"id", "path", "reason"}, {"count", "type", "feature"})
        kind, file, anchor, replacement = "delete", rel_path(entry["path"], f"{where}.path", glob=True), None, None
    else:
        entry = _keys(entry, where, {"id", "file", "anchor", "reason"}, {"kind", "replacement", "count", "type", "feature"})
        kind = entry.get("kind", "replace")
        if kind not in ("replace", "remove-section"):
            raise ConfigError(f"{where}: kind must be \"replace\" or \"remove-section\"")
        file = rel_path(entry["file"], f"{where}.file", glob=True)
        anchor = _text(entry["anchor"], f"{where}.anchor")
        if not anchor:
            raise ConfigError(f"{where}: anchor must not be empty")
        if kind == "replace":
            if "replacement" not in entry:
                raise ConfigError(f"{where}: a replace rule needs a replacement (use \"\" to delete the anchor)")
            replacement = _text(entry["replacement"], f"{where}.replacement")
        else:
            if "replacement" in entry:
                raise ConfigError(f"{where}: remove-section takes no replacement")
            if "\n" in anchor or not re.match(r"^#{1,6} \S", anchor):
                raise ConfigError(f"{where}: a remove-section anchor is one Markdown heading line, e.g. \"## Title\"")
            replacement = None
    rid = _str(entry["id"], f"{where}.id")
    if not ID_RE.match(rid):
        raise ConfigError(f"{where}: id {rid!r} must match {ID_RE.pattern}")
    rtype = entry.get("type", "strict")
    if rtype not in ("strict", "feature"):
        raise ConfigError(f"{where}: type must be \"strict\" or \"feature\"")
    feature = entry.get("feature")
    if rtype == "feature":
        if feature not in fork.features:
            raise ConfigError(f"{where}: feature rule names unknown feature {feature!r}; declare it under \"features\"")
    elif feature is not None:
        raise ConfigError(f"{where}: only a \"type\": \"feature\" rule names a feature")
    return Rule(rid, kind, file, anchor, replacement, _int(entry.get("count", 1), f"{where}.count"),
                feature, _str(entry["reason"], f"{where}.reason"))


def upstream_relative_to_output(fork: Fork, path: str) -> Optional[Tuple[PathMap, str]]:
    """(mapping, path inside that output dir) for an upstream path in an output, else None."""
    for mapping in fork.paths:
        if path == mapping.upstream:
            return mapping, PurePosixPath(path).name
        if path.startswith(mapping.upstream + "/"):
            return mapping, path[len(mapping.upstream) + 1:]
    return None
