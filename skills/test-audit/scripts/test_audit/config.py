"""Optional `.test-audit.json` overrides. Detection works without it.

Validation is total: every key, nested key, list element and command template
is checked, and any problem is a usage error (exit 2), never a traceback."""

import json
import os
import shlex

import re

from .core import UsageError, glob_regex, placeholder_problems

_TEMPLATE = re.compile(r"\{(files|file|root)\}")

CLASSES = ("test", "support", "production", "docs", "config", "generated", "ignore")
RUNNERS = ("vitest", "vp", "jest", "bun", "pytest")
ANALYZERS = ("knip", "vulture", "deptry")


def _err(where, msg):
    raise UsageError("config: %s %s" % (where, msg))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _str(where, v):
    if not isinstance(v, str) or not v:
        _err(where, "must be a non-empty string")
    if "\0" in v:
        _err(where, "contains a NUL byte")


def _command(where, v):
    _str(where, v)
    try:
        words = shlex.split(v)
    except ValueError as e:
        _err(where, "is not a valid command (%s)" % e)
    if not words:
        _err(where, "is an empty command")
    for word in words:
        if _TEMPLATE.fullmatch(word):
            continue
        bad = placeholder_problems(word, _TEMPLATE)
        if bad:
            _err(where, "has %s that %s in %r; put placeholders in plain unquoted, unescaped position "
                        "(test-audit shell-quotes embedded values itself)" % (bad[0][0], bad[0][1], word))


def _list_of(where, v, check):
    if not isinstance(v, list):
        _err(where, "must be a list")
    for i, item in enumerate(v):
        check("%s[%d]" % (where, i), item)


def _keys(where, d, allowed=None):
    """d must be an object; with `allowed`, every key must be in it."""
    if not isinstance(d, dict):
        _err(where, "must be an object")
    for k in d:
        if "\0" in k:
            _err(where, "has a key containing a NUL byte")
    if allowed is None:
        return
    unknown = sorted(k for k in d if k not in allowed)
    if unknown:
        _err(where, "has unknown keys: %s (allowed: %s)" % (", ".join(unknown), ", ".join(sorted(allowed))))


def _glob(where, v):
    _str(where, v)
    try:
        glob_regex(v)
    except UsageError as e:
        _err(where, "is not a valid glob (%s)" % e)


def _runner(where, v):
    if v not in RUNNERS:
        _err(where, "must be one of %s" % ", ".join(RUNNERS))


def _exit_code(where, v):
    if not _is_int(v) or not 0 <= v <= 255:
        _err(where, "must be an integer exit code 0-255")


def validate(data):
    """Top-level keys starting with "$" (e.g. "$schema", "$comment") are
    metadata: accepted and ignored, values unchecked. Nowhere else."""
    top = {"baseBranch", "testCommand", "testErrorExitCodes", "roots", "runners", "checks", "classify", "testGlobs", "deadcode"}
    _keys("(top level)", {k: v for k, v in data.items() if not k.startswith("$")}, top)
    if "baseBranch" in data:
        _str("baseBranch", data["baseBranch"])
    if "testCommand" in data:
        _command("testCommand", data["testCommand"])
    if "testErrorExitCodes" in data:
        _list_of("testErrorExitCodes", data["testErrorExitCodes"], _exit_code)
    if "testGlobs" in data:
        _list_of("testGlobs", data["testGlobs"], _glob)
    if "roots" in data:
        _keys("roots", data["roots"])
        for rel, spec in data["roots"].items():
            where = "roots[%s]" % json.dumps(rel)
            _keys(where, spec, {"runner", "testCommand", "python"})
            if "runner" in spec:
                _runner(where + ".runner", spec["runner"])
            if "testCommand" in spec:
                _command(where + ".testCommand", spec["testCommand"])
            if "python" in spec:
                _str(where + ".python", spec["python"])
    if "runners" in data:
        if not isinstance(data["runners"], list):
            _err("runners", "must be a list")
        for i, spec in enumerate(data["runners"]):
            where = "runners[%d]" % i
            _keys(where, spec, {"glob", "runner", "testCommand"})
            if "glob" not in spec:
                _err(where, "needs glob")
            _glob(where + ".glob", spec["glob"])
            if ("runner" in spec) == ("testCommand" in spec):
                _err(where, "needs exactly one of runner or testCommand")
            if "runner" in spec:
                _runner(where + ".runner", spec["runner"])
            else:
                _command(where + ".testCommand", spec["testCommand"])
    if "checks" in data:
        _keys("checks", data["checks"])
        for name, cmd in data["checks"].items():
            if cmd is not None:
                _command("checks[%s]" % json.dumps(name), cmd)
    if "classify" in data:
        _keys("classify", data["classify"], set(CLASSES))
        for cls, globs in data["classify"].items():
            _list_of("classify.%s" % cls, globs, _glob)
    if "deadcode" in data:
        dc = data["deadcode"]
        _keys("deadcode", dc, {"knipPackage", "vulturePackage", "deptryPackage", "vultureMinConfidence", "vultureWhitelist", "disable"})
        for k in ("knipPackage", "vulturePackage", "deptryPackage"):
            if k in dc:
                _str("deadcode." + k, dc[k])
        if "vultureMinConfidence" in dc and (not _is_int(dc["vultureMinConfidence"]) or not 0 <= dc["vultureMinConfidence"] <= 100):
            _err("deadcode.vultureMinConfidence", "must be an integer 0-100")
        if "vultureWhitelist" in dc:
            _list_of("deadcode.vultureWhitelist", dc["vultureWhitelist"], _str)
        if "disable" in dc:
            def _analyzer(where, v):
                if v not in ANALYZERS:
                    _err(where, "must be one of %s" % ", ".join(ANALYZERS))
            _list_of("deadcode.disable", dc["disable"], _analyzer)


def load(repo, explicit_path=None):
    """Return (config_dict, path_or_None, applied_keys)."""
    if explicit_path:
        path = os.path.abspath(explicit_path)
        if not os.path.isfile(path):
            raise UsageError("config file not found: %s" % explicit_path)
    else:
        path = os.path.join(repo, ".test-audit.json")
        if not os.path.isfile(path):
            return {}, None, []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        raise UsageError("config %s is not valid JSON: %s" % (path, e)) from None
    if not isinstance(data, dict):
        raise UsageError("config %s must be a JSON object" % path)
    validate(data)
    applied = sorted(k for k in data if not k.startswith("$"))
    return data, path, applied
