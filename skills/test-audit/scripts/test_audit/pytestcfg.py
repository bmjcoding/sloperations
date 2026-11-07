"""pytest's active configuration for a path, discovered the way pytest does:
walk up from the test file's directory and, in each directory, take the
first of pytest.toml, .pytest.toml, pytest.ini, .pytest.ini, pyproject.toml
([tool.pytest] / [tool.pytest.ini_options]), tox.ini ([pytest]),
setup.cfg ([tool:pytest]). pytest.ini always matches, even when empty.

Classification (which files are tests) and the ledger (which functions and
classes are tests) share this one source."""

import configparser
import fnmatch
import glob
import os
import shlex

from . import tomlmini

DEFAULTS = {
    "python_files": ["test_*.py", "*_test.py"],
    "python_classes": ["Test*"],
    "python_functions": ["test*"],
    "testpaths": [],
}
KEYS = tuple(DEFAULTS)


def _words(v):
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        try:
            return shlex.split(v)
        except ValueError:
            return v.split()
    return None


def _ini(path, section):
    cp = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cp.read_file(f)
    except (OSError, configparser.Error, UnicodeDecodeError):
        return None
    if not cp.has_section(section):
        return None
    return {k: cp.get(section, k) for k in KEYS if cp.has_option(section, k)}


def _toml(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return tomlmini.loads(f.read())
    except (OSError, tomlmini.TomlError, UnicodeDecodeError):
        return None


def _read(dirpath):
    """(filename, values) for the config pytest would pick in dirpath, or None."""
    for name in ("pytest.toml", ".pytest.toml"):
        p = os.path.join(dirpath, name)
        if os.path.isfile(p):
            data = _toml(p) or {}
            return name, data.get("pytest") or {}
    for name in ("pytest.ini", ".pytest.ini"):
        p = os.path.join(dirpath, name)
        if os.path.isfile(p):
            return name, _ini(p, "pytest") or {}
    p = os.path.join(dirpath, "pyproject.toml")
    if os.path.isfile(p):
        tool = ((_toml(p) or {}).get("tool") or {})
        pt = tool.get("pytest")
        if isinstance(pt, dict):
            if isinstance(pt.get("ini_options"), dict):
                return "pyproject.toml", pt["ini_options"]
            return "pyproject.toml", {k: v for k, v in pt.items() if k in KEYS}
    p = os.path.join(dirpath, "tox.ini")
    if os.path.isfile(p):
        v = _ini(p, "pytest")
        if v is not None:
            return "tox.ini", v
    p = os.path.join(dirpath, "setup.cfg")
    if os.path.isfile(p):
        v = _ini(p, "tool:pytest")
        if v is not None:
            return "setup.cfg", v
    return None


def for_dir(ctx, rel_dir):
    """Active pytest settings for files in rel_dir (repo-relative)."""
    key = ("pytestcfg", rel_dir)
    if key in ctx.cache:
        return ctx.cache[key]
    found = _read(ctx.abspath(rel_dir))
    if found:
        name, raw = found
        cfg = {"path": name if rel_dir == "." else rel_dir + "/" + name, "dir": rel_dir}
        for k in KEYS:
            w = _words(raw.get(k)) if k in raw else None
            # a key that is present wins even when empty: `python_functions = []`
            # makes pytest collect nothing, so it must here too
            cfg[k] = w if w is not None else list(DEFAULTS[k])
    elif rel_dir == ".":
        cfg = {"path": None, "dir": "."}
        cfg.update({k: list(v) for k, v in DEFAULTS.items()})
    else:
        cfg = for_dir(ctx, os.path.dirname(rel_dir) or ".")
    ctx.cache[key] = cfg
    return cfg


def for_file(ctx, rel):
    return for_dir(ctx, os.path.dirname(rel) or ".")


def is_test_file(ctx, rel):
    """Would pytest collect this file when given it explicitly? (`testpaths`
    does not matter here: it only scopes argument-less discovery.)"""
    if not rel.endswith(".py"):
        return False
    cfg = for_file(ctx, rel)
    base = os.path.basename(rel)
    return any(fnmatch.fnmatchcase(rel if "/" in p else base, p if "/" not in p else "*" + p.lstrip("*"))
               for p in cfg["python_files"])


def _testpath_targets(ctx, cfg):
    """pytest 9.2 `Config._decide_args` for an argument-less run from the
    rootdir: each testpaths entry is expanded with glob.iglob(entry,
    recursive=True) relative to the rootdir; if nothing at all matches,
    pytest warns and searches recursively from the invocation dir instead
    (here: no filter). Returns repo-relative matches, or None = no filter."""
    key = ("testpath-targets", cfg["dir"])
    if key in ctx.cache:
        return ctx.cache[key]
    anchor = ctx.abspath(cfg["dir"])
    found = []
    for entry in cfg["testpaths"]:
        for hit in sorted(glob.iglob(os.path.join(glob.escape(anchor), entry), recursive=True)):
            rel = os.path.relpath(hit, ctx.repo).replace(os.sep, "/")
            found.append("." if rel == "." else rel)
    result = found or None
    ctx.cache[key] = result
    return result


def in_testpaths(ctx, rel):
    """Would argument-less discovery (`pytest` with no paths) reach rel?"""
    cfg = for_file(ctx, rel)
    if not cfg["testpaths"]:
        return True
    targets = _testpath_targets(ctx, cfg)
    if targets is None:
        return True
    return any(t == "." or rel == t or rel.startswith(t.rstrip("/") + "/") for t in targets)
