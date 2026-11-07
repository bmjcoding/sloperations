"""Repository detection: ecosystems, package roots, package managers, runners,
configured checks, dead-code analyzers. Reads files only; runs nothing."""

import json
import os
import re
import shutil

from . import tomlmini
from .core import capture, glob_match

JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
TS_EXTS = (".ts", ".tsx", ".mts", ".cts")
PY_EXTS = (".py", ".pyi")
CONF_EXTS = ("ts", "mts", "cts", "js", "mjs", "cjs", "json")
LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"),
    ("package-lock.json", "npm"),
    ("npm-shrinkwrap.json", "npm"),
    ("yarn.lock", "yarn"),
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
)
# Directories whose contents are never package roots (fixture projects etc.).
NON_ROOT_SEGMENTS = {
    "node_modules", "fixtures", "fixture", "__fixtures__", "__mocks__", "mocks",
    "test", "tests", "__tests__", "testdata", "test-fixtures", "vendor", ".venv",
    "venv", "site-packages", "dist", "build",
}
PY_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")
PY_TOP_MARKERS = PY_MARKERS + (
    "requirements.txt", "requirements-dev.txt", "Pipfile", "uv.lock", "pytest.ini",
    ".pytest.ini", "pytest.toml", ".pytest.toml", "tox.ini", "conftest.py", "poetry.lock", "pdm.lock",
)


def read_text(path, limit=512 * 1024):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return None


def read_json(path):
    text = read_text(path, 8 << 20)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def read_toml(ctx, path):
    text = read_text(path, 8 << 20)
    if text is None:
        return None
    try:
        return tomlmini.loads(text)
    except tomlmini.TomlError as e:
        ctx.warn("could not parse %s: %s" % (ctx.relpath(path), e))
        return {}


def _dir(rel):
    d = os.path.dirname(rel)
    return d if d else "."


def _join(root, name):
    return name if root == "." else root + "/" + name


def under(path, root):
    return root == "." or path == root or path.startswith(root + "/")


def _segments_blocked(d):
    return d != "." and any(seg in NON_ROOT_SEGMENTS for seg in d.split("/"))


def _outermost(dirs):
    out = []
    for d in sorted(dirs, key=lambda x: (x.count("/"), x)):
        if not any(under(d, o) for o in out):
            out.append(d)
    return sorted(out)


def _expand(patterns, dirs):
    inc = [p.rstrip("/") for p in patterns if not p.startswith("!")]
    exc = [p[1:].rstrip("/") for p in patterns if p.startswith("!")]
    res = []
    for d in dirs:
        if d == ".":
            continue
        if glob_match(d, inc) and not glob_match(d, exc):
            res.append(d)
    return res


def _pnpm_workspace_patterns(text):
    pats = []
    in_pkgs = False
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t", "-")):
            in_pkgs = False
            m = re.match(r"packages\s*:\s*(\[.*\])?\s*$", line)
            if m:
                in_pkgs = True
                if m.group(1):
                    for item in m.group(1).strip("[]").split(","):
                        item = item.strip().strip("'\"")
                        if item:
                            pats.append(item)
                    in_pkgs = False
            continue
        if in_pkgs:
            m = re.match(r"\s*-\s*(.+)$", line)
            if m:
                pats.append(m.group(1).strip().strip("'\""))
    return pats


class Root:
    def __init__(self, path, ecosystem):
        self.path = path
        self.ecosystem = ecosystem
        self.name = None
        self.manifest = None  # parsed package.json / pyproject
        self.pm = None
        self.runner = None  # resolved runner name or None
        self.runner_source = None
        self.runners_detected = []  # [{"runner", "signals": [...]}]
        self.ambiguous = False
        self.own_runner_signals = False
        self.test_script = None
        self.checks = []
        self.pytest_config = None  # active pytest settings for the root dir
        self.overrides = {}

    def to_json(self):
        d = {
            "path": self.path,
            "ecosystem": self.ecosystem,
            "name": self.name,
            "package_manager": self.pm,
            "runner": self.runner,
            "runner_source": self.runner_source,
            "runners_detected": self.runners_detected,
            "ambiguous": self.ambiguous,
            "test_script": self.test_script,
            "checks": [c.to_json() for c in self.checks],
        }
        if self.overrides:
            d["overrides"] = self.overrides
        if self.pytest_config is not None:
            d["pytest_config"] = self.pytest_config
        return d


class Check:
    def __init__(self, name, root, argv, accepts_files, exts, available, source):
        self.name = name
        self.root = root
        self.argv = argv  # template; "{files}" token expands to file args
        self.accepts_files = accepts_files
        self.exts = exts
        self.available = available
        self.source = source

    def to_json(self):
        return {
            "name": self.name,
            "root": self.root,
            "command": self.argv,
            "accepts_files": self.accepts_files,
            "available": self.available,
            "source": self.source,
        }


# --------------------------------------------------------------------------
# binaries


def find_node_bin(ctx, root, name):
    """Absolute path to node_modules/.bin/<name>, nearest first, or None."""
    d = root
    while True:
        cand = os.path.join(ctx.abspath(d), "node_modules", ".bin", name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
        if d == ".":
            return None
        d = _dir(d)


def yarn_pnp(ctx):
    return os.path.isfile(os.path.join(ctx.repo, ".pnp.cjs")) or os.path.isfile(
        os.path.join(ctx.repo, ".pnp.js")
    )


BIN_PACKAGES = {
    "tsc": "typescript", "tsgo": "@typescript/native-preview", "biome": "@biomejs/biome",
    "vp": "vite-plus",
}


def _declares(ctx, root, package):
    d = root
    while True:
        pkg = read_json(os.path.join(ctx.abspath(d), "package.json")) or {}
        if package in _deps(pkg):
            return True
        if d == ".":
            return False
        d = _dir(d)


def node_tool(ctx, root, name, pm=None):
    """(argv_prefix, source) for a repo-installed JS tool, or (None, reason).
    The prefix is an absolute path; callers relativize it for portability."""
    b = find_node_bin(ctx, root, name)
    if b:
        return [b], "installed:" + ctx.relpath(b)
    if (pm or {}).get("name") == "yarn" and yarn_pnp(ctx) and _declares(ctx, root, BIN_PACKAGES.get(name, name)):
        return ["yarn", "run", "--binaries-only", name], "yarn-pnp"
    return None, "not installed (no node_modules/.bin/%s)" % name


def rel_exe(path, cwd_abs):
    """Executable path relative to cwd (kept as typed: venv symlinks matter)."""
    if not os.path.isabs(path):
        return path
    rel = os.path.relpath(path, cwd_abs)
    if rel.startswith(".." + os.sep + ".." + os.sep + ".." + os.sep + ".."):
        return path
    rel = rel.replace(os.sep, "/")
    return rel if "/" in rel else "./" + rel


def lexically_inside(ctx, path):
    ap = os.path.abspath(path)
    for top in {ctx.repo, os.path.abspath(ctx.repo)}:
        if ap == top or ap.startswith(top + os.sep):
            return True
    return False


def posix_rel(ctx, p):
    return os.path.relpath(os.path.abspath(p), ctx.repo).replace(os.sep, "/")


def venv_candidates(ctx, root):
    cands = []
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        cands.append(("VIRTUAL_ENV", venv))
    cands.append((".venv", os.path.join(ctx.abspath(root), ".venv")))
    if root != ".":
        cands.append((".venv", os.path.join(ctx.repo, ".venv")))
    out = []
    for src, v in cands:
        for sub in ("bin", "Scripts"):
            if os.path.isdir(os.path.join(v, sub)):
                out.append((src, os.path.join(v, sub)))
                break
    return out


def python_tool(ctx, root, name):
    for _src, bindir in venv_candidates(ctx, root):
        for exe in (name, name + ".exe"):
            p = os.path.join(bindir, exe)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return [p], "venv:" + (posix_rel(ctx, p) if lexically_inside(ctx, p) else p)
    return None, "not installed in the project environment"


def python_interpreter(ctx, root):
    """(python_path, source). Never creates an environment."""
    override = ctx.config.get("roots", {}).get(root, {}).get("python")
    if override:
        p = override if os.path.isabs(override) else os.path.join(ctx.abspath(root), override)
        return p, "config"
    for src, bindir in venv_candidates(ctx, root):
        for exe in ("python", "python3", "python.exe"):
            p = os.path.join(bindir, exe)
            if os.path.isfile(p):
                return p, src
    for exe in ("python3", "python"):
        w = shutil.which(exe)
        if w:
            return w, "PATH"
    return None, "no interpreter"


# --------------------------------------------------------------------------
# detection


def _deps(pkg):
    out = set()
    for k in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        v = (pkg or {}).get(k)
        if isinstance(v, dict):
            out.update(v)
    return out


def _conf_files(ctx, root, stems):
    found = []
    base = ctx.abspath(root)
    try:
        names = set(os.listdir(base))
    except OSError:
        return found
    for stem in stems:
        for ext in CONF_EXTS:
            n = "%s.%s" % (stem, ext)
            if n in names:
                found.append(_join(root, n))
    return found


def _script_runner(script):
    if not script:
        return None
    s = " " + script + " "
    if re.search(r"[\s/]vp\s+test\b", s) or re.search(r"\bvp\s+run\s+test\b", s):
        return "vp"
    if re.search(r"\bbun\s+(?:run\s+)?test\b", s) and "vitest" not in s and "jest" not in s:
        return "bun"
    if re.search(r"\bvitest\b", s):
        return "vitest"
    if re.search(r"\bjest\b", s):
        return "jest"
    if re.search(r"\bpytest\b", s):
        return "pytest"
    return None


def _js_runner_signals(ctx, root, pkg, top_pkg):
    """{family_runner: [signals]} with strength flags."""
    sig = {}

    def add(runner, s, strong):
        sig.setdefault(runner, {"signals": [], "strong": False})
        sig[runner]["signals"].append(s)
        sig[runner]["strong"] = sig[runner]["strong"] or strong

    own = _deps(pkg)
    for dep, runner in (("vite-plus", "vp"), ("vitest", "vitest"), ("jest", "jest")):
        if dep in own:
            add(runner, "dependency:" + dep, False)
    if "bun-types" in own or "@types/bun" in own:
        add("bun", "dependency:" + ("@types/bun" if "@types/bun" in own else "bun-types"), False)
    for f in _conf_files(ctx, root, ("vitest.config", "vitest.workspace", "vitest.projects")):
        add("vitest", "config:" + f, True)
    for f in _conf_files(ctx, root, ("vite.config",)):
        text = read_text(ctx.abspath(f)) or ""
        if re.search(r"""from\s+['"]vite-plus['"]""", text):
            add("vp", "config:" + f, True)
        elif re.search(r"\btest\s*:", text):
            add("vitest", "config:" + f, True)
    for f in _conf_files(ctx, root, ("jest.config",)):
        add("jest", "config:" + f, True)
    if isinstance((pkg or {}).get("jest"), dict):
        add("jest", "package.json#jest", True)
    bunfig = ctx.abspath(_join(root, "bunfig.toml"))
    if os.path.isfile(bunfig) and re.search(r"^\s*\[test\]", read_text(bunfig) or "", re.M):
        add("bun", "config:" + _join(root, "bunfig.toml"), True)
    script = ((pkg or {}).get("scripts") or {}).get("test")
    sr = _script_runner(script)
    if sr and sr != "pytest":
        add(sr, "scripts.test", True)
    return sig, script


def _resolve_js_runner(sig, script):
    """Return (runner, source, ambiguous)."""
    families = {}
    for runner, info in sig.items():
        fam = "vitest" if runner in ("vitest", "vp") else runner
        families.setdefault(fam, []).append((runner, info))
    strong = {f for f, items in families.items() if any(i["strong"] for _, i in items)}
    pick = strong if strong else set(families)
    sr = _script_runner(script)
    if len(pick) > 1:
        # bun types alongside another runner is not a runner signal by itself
        if "bun" in pick and not any(i["strong"] for _, i in families["bun"]):
            pick.discard("bun")
    if len(pick) > 1:
        return None, None, True
    if not pick:
        return None, None, False
    fam = pick.pop()
    if fam == "vitest":
        runners = {r for r, _ in families["vitest"]}
        if sr in ("vp", "vitest"):
            return sr, "scripts.test", False
        if "vp" in runners:
            return "vp", "vite-plus", False
        return "vitest", "signals", False
    return fam, "scripts.test" if sr == fam else "signals", False


def _py_deps(proj):
    names = set()

    def add(spec):
        if isinstance(spec, str):
            m = re.match(r"\s*([A-Za-z0-9_.\-]+)", spec)
            if m:
                names.add(m.group(1).lower().replace("_", "-"))

    p = proj.get("project") or {}
    for s in p.get("dependencies") or []:
        add(s)
    for group in (p.get("optional-dependencies") or {}).values():
        for s in group or []:
            add(s)
    for group in (proj.get("dependency-groups") or {}).values():
        for s in group or []:
            add(s)
    tool = proj.get("tool") or {}
    for s in (tool.get("uv") or {}).get("dev-dependencies") or []:
        add(s)
    poetry = tool.get("poetry") or {}
    for k in (poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}):
        names.update(x.lower() for x in k)
    for g in (poetry.get("group") or {}).values():
        names.update(x.lower() for x in (g.get("dependencies") or {}))
    return names


def _ini_has(path, section):
    text = read_text(path)
    return bool(text and re.search(r"^\s*\[%s\]" % re.escape(section), text, re.M))


def detection(ctx):
    if "detection" in ctx.cache:
        return ctx.cache["detection"]
    files = ctx.files()
    cfg_roots = ctx.config.get("roots", {})
    roots = []
    workspaces = {}

    # ---- JS roots
    pkg_dirs = sorted({_dir(f) for f in files if os.path.basename(f) == "package.json"})
    top_pkg = read_json(os.path.join(ctx.repo, "package.json")) if "." in pkg_dirs else None
    js_dirs = []
    if top_pkg is not None:
        js_dirs.append(".")
        bases = ["."]
    else:
        bases = _outermost([d for d in pkg_dirs if not _segments_blocked(d)])
        js_dirs += bases
    for base in bases:
        pats, source = [], None
        wsfile = ctx.abspath(_join(base, "pnpm-workspace.yaml"))
        if os.path.isfile(wsfile):
            pats = _pnpm_workspace_patterns(read_text(wsfile) or "")
            source = _join(base, "pnpm-workspace.yaml")
        else:
            bpkg = read_json(ctx.abspath(_join(base, "package.json"))) or {}
            ws = bpkg.get("workspaces")
            if isinstance(ws, dict):
                ws = ws.get("packages")
            if isinstance(ws, list):
                pats = [p for p in ws if isinstance(p, str)]
                source = _join(base, "package.json#workspaces")
        if pats:
            full = [p if base == "." else (("!" + base + "/" + p[1:]) if p.startswith("!") else base + "/" + p) for p in pats]
            members = _expand(full, pkg_dirs)
            workspaces.setdefault("js", []).append({"source": source, "patterns": pats, "members": members})
            js_dirs += members
    for d in sorted(set(js_dirs)):
        r = Root(d, "js")
        r.manifest = read_json(ctx.abspath(_join(d, "package.json"))) or {}
        r.name = r.manifest.get("name")
        roots.append(r)

    # ---- Python roots
    py_marker_dirs = sorted({_dir(f) for f in files if os.path.basename(f) in PY_MARKERS})
    top_names = {f for f in files if "/" not in f}
    has_py_tests = any(
        f.endswith(".py") and _default_py_test(os.path.basename(f)) and not _segments_fixture(f)
        for f in files
    )
    py_dirs = []
    if any(m in top_names for m in PY_TOP_MARKERS) or any(
        f.startswith("requirements") and f.endswith(".txt") for f in top_names
    ):
        py_bases = ["."]
    else:
        py_bases = _outermost([d for d in py_marker_dirs if not _segments_blocked(d)])
    py_dirs += py_bases
    for base in py_bases:
        pp = ctx.abspath(_join(base, "pyproject.toml"))
        if not os.path.isfile(pp):
            continue
        proj = read_toml(ctx, pp) or {}
        ws = ((proj.get("tool") or {}).get("uv") or {}).get("workspace") or {}
        mem = ws.get("members") or []
        if mem:
            exc = ["!" + x for x in (ws.get("exclude") or [])]
            pyproj_dirs = sorted({_dir(f) for f in files if os.path.basename(f) == "pyproject.toml"})
            full = [m if base == "." else base + "/" + m for m in mem] + [
                e if base == "." else "!" + base + "/" + e[1:] for e in exc
            ]
            members = _expand(full, pyproj_dirs)
            workspaces.setdefault("python", []).append(
                {"source": _join(base, "pyproject.toml#tool.uv.workspace"), "patterns": mem, "members": members}
            )
            py_dirs += members
    if has_py_tests and not py_dirs:
        py_dirs.append(".")
    for d in sorted(set(py_dirs)):
        r = Root(d, "python")
        pp = ctx.abspath(_join(d, "pyproject.toml"))
        r.manifest = (read_toml(ctx, pp) or {}) if os.path.isfile(pp) else {}
        r.name = (r.manifest.get("project") or {}).get("name") or ((r.manifest.get("tool") or {}).get("poetry") or {}).get("name")
        roots.append(r)

    roots.sort(key=lambda r: (r.path, r.ecosystem))
    det = {"roots": roots, "workspaces": workspaces, "top_pkg": top_pkg}
    ctx.cache["detection"] = det

    for r in roots:
        if r.path in cfg_roots:
            r.overrides = dict(cfg_roots[r.path])
        if r.ecosystem == "js":
            _detect_js_root(ctx, r, top_pkg)
        else:
            _detect_py_root(ctx, r)
    for rel in cfg_roots:
        if not any(r.path == rel for r in roots):
            ctx.warn("config roots.%s does not match any detected package root" % rel)
    return det


def _segments_fixture(path):
    return any(seg in ("fixtures", "fixture", "__fixtures__", "__mocks__", "mocks", "testdata", "node_modules", ".venv", "venv", "site-packages") for seg in path.split("/")[:-1])


def _default_py_test(base):
    return (base.startswith("test_") or base.endswith("_test.py")) and base.endswith(".py")


def _detect_pm(ctx, root, pkg, top_pkg):
    for src_pkg, where in ((pkg, root), (top_pkg, ".")):
        pmf = (src_pkg or {}).get("packageManager")
        if isinstance(pmf, str) and pmf:
            name, _, ver = pmf.partition("@")
            return {"name": name, "version": ver.split("+")[0] or None, "source": "packageManager:" + _join(where, "package.json")}
    for where in ([root, "."] if root != "." else ["."]):
        found = [(lf, pm) for lf, pm in LOCKFILES if os.path.isfile(ctx.abspath(_join(where, lf)))]
        if found:
            names = sorted({pm for _, pm in found})
            if len(names) > 1:
                ctx.warn("multiple lockfiles in %s (%s); using %s. Set packageManager in package.json to settle it."
                         % (where, ", ".join(lf for lf, _ in found), found[0][1]))
            return {"name": found[0][1], "version": None, "source": "lockfile:" + _join(where, found[0][0])}
    return {"name": "npm", "version": None, "source": "default"}


def _detect_js_root(ctx, r, top_pkg):
    pkg = r.manifest
    r.pm = _detect_pm(ctx, r.path, pkg, top_pkg)
    if r.pm["name"] == "yarn":
        r.pm["pnp"] = yarn_pnp(ctx)
    sig, script = _js_runner_signals(ctx, r.path, pkg, top_pkg)
    r.test_script = script
    r.own_runner_signals = bool(sig)
    r.runners_detected = [
        {"runner": k, "signals": v["signals"], "strong": v["strong"]} for k, v in sorted(sig.items())
    ]
    if r.overrides.get("runner"):
        r.runner, r.runner_source, r.ambiguous = r.overrides["runner"], "config", False
        r.own_runner_signals = True
    elif r.overrides.get("testCommand"):
        r.runner, r.runner_source, r.ambiguous = "custom", "config", False
        r.own_runner_signals = True
    else:
        r.runner, r.runner_source, r.ambiguous = _resolve_js_runner(sig, script)
        if not sig and r.path == "." and r.pm["name"] == "bun":
            r.runner, r.runner_source = "bun", "package manager is bun (bun's own runner)"
            r.own_runner_signals = True
    _js_checks(ctx, r, top_pkg)


def _detect_py_root(ctx, r):
    proj = r.manifest
    tool = proj.get("tool") or {}
    base = ctx.abspath(r.path)
    if os.path.isfile(os.path.join(base, "uv.lock")) or "uv" in tool or os.path.isfile(os.path.join(ctx.repo, "uv.lock")):
        src = "uv.lock" if os.path.isfile(os.path.join(base, "uv.lock")) or os.path.isfile(os.path.join(ctx.repo, "uv.lock")) else "pyproject.toml#tool.uv"
        r.pm = {"name": "uv", "version": None, "source": src}
    elif os.path.isfile(os.path.join(base, "poetry.lock")) or "poetry" in tool:
        r.pm = {"name": "poetry", "version": None, "source": "poetry"}
    elif os.path.isfile(os.path.join(base, "pdm.lock")):
        r.pm = {"name": "pdm", "version": None, "source": "pdm.lock"}
    else:
        r.pm = {"name": "pip", "version": None, "source": "default"}
    signals = []
    deps = _py_deps(proj)
    if "pytest" in deps:
        signals.append("dependency:pytest")
    for req in ("requirements-dev.txt", "requirements-test.txt", "requirements.txt", "dev-requirements.txt"):
        t = read_text(os.path.join(base, req))
        if t and re.search(r"^\s*pytest\b", t, re.M):
            signals.append("dependency:" + _join(r.path, req))
    ini = (tool.get("pytest") or {}).get("ini_options")
    if ini is not None or "pytest" in tool:
        signals.append("config:" + _join(r.path, "pyproject.toml#tool.pytest"))
    for name, section in (("pytest.ini", "pytest"), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        if _ini_has(os.path.join(base, name), section):
            signals.append("config:" + _join(r.path, name))
    if os.path.isfile(os.path.join(base, "conftest.py")):
        signals.append("config:" + _join(r.path, "conftest.py"))
    from . import pytestcfg

    r.pytest_config = pytestcfg.for_dir(ctx, r.path)
    if r.pytest_config.get("path") and "config:" + r.pytest_config["path"] not in signals:
        signals.append("config:" + r.pytest_config["path"])
    r.runners_detected = [{"runner": "pytest", "signals": signals or ["default"], "strong": bool(signals)}]
    r.own_runner_signals = True
    if r.overrides.get("testCommand"):
        r.runner, r.runner_source = "custom", "config"
    else:
        r.runner, r.runner_source = "pytest", "signals" if signals else "default (pytest also runs unittest)"
    _py_checks(ctx, r)


# --------------------------------------------------------------------------
# checks


def _has_conf(ctx, root, names):
    for d in ([root, "."] if root != "." else ["."]):
        for n in names:
            if os.path.exists(ctx.abspath(_join(d, n))):
                return _join(d, n)
    return None


def _js_checks(ctx, r, top_pkg):
    deps = _deps(r.manifest) | (_deps(top_pkg) if r.path != "." else set())
    lint_exts = JS_EXTS + (".vue", ".svelte", ".astro")
    fmt_exts = lint_exts + (".json", ".jsonc", ".md", ".mdx", ".css", ".scss", ".less", ".html", ".yaml", ".yml", ".graphql")
    specs = []
    oxl = _has_conf(ctx, r.path, (".oxlintrc.json", "oxlintrc.json", "oxlint.config.ts"))
    if "oxlint" in deps or oxl:
        specs.append(("oxlint", "oxlint", ["{files}"], True, lint_exts))
    esl = _has_conf(ctx, r.path, ["eslint.config." + e for e in CONF_EXTS] + [".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml"])
    if esl or isinstance(r.manifest.get("eslintConfig"), dict):
        specs.append(("eslint", "eslint", ["{files}"], True, lint_exts))
    if _has_conf(ctx, r.path, ("biome.json", "biome.jsonc")) or "@biomejs/biome" in deps:
        specs.append(("biome", "biome", ["check", "--no-errors-on-unmatched", "{files}"], True, fmt_exts))
    prc = _has_conf(ctx, r.path, [".prettierrc", ".prettierrc.json", ".prettierrc.yaml", ".prettierrc.yml", ".prettierrc.json5", ".prettierrc.toml"] + [".prettierrc." + e for e in CONF_EXTS] + ["prettier.config." + e for e in CONF_EXTS])
    if prc or "prettier" in deps or "prettier" in r.manifest:
        specs.append(("prettier", "prettier", ["--check", "--ignore-unknown", "{files}"], True, fmt_exts))
    if "oxfmt" in deps or _has_conf(ctx, r.path, (".oxfmtrc.json", ".oxfmtrc.jsonc")):
        specs.append(("oxfmt", "oxfmt", ["--check", "{files}"], True, fmt_exts))
    if "vite-plus" in deps:
        specs.append(("vp check", "vp", ["check", "--no-error-on-unmatched-pattern", "{files}"], True, fmt_exts))
    tsconfig = os.path.isfile(ctx.abspath(_join(r.path, "tsconfig.json")))
    ts_args = ["--noEmit", "-p", "tsconfig.json"]
    if tsconfig and "typescript" in deps:
        specs.append(("tsc", "tsc", ts_args, False, TS_EXTS))
    if tsconfig and "@typescript/native-preview" in deps:
        specs.append(("tsgo", "tsgo", ts_args, False, TS_EXTS))
    overrides = ctx.config.get("checks", {})
    for name, binname, args, files_ok, exts in specs:
        if name in overrides:
            continue
        if binname == "vp":
            prefix = [find_node_bin(ctx, r.path, "vp") or shutil.which("vp")]
            src = "installed" if prefix[0] else "not installed"
            if not prefix[0]:
                prefix = None
        else:
            prefix, src = node_tool(ctx, r.path, binname, r.pm)
        if prefix and binname in ("tsc", "tsgo") and _ts_incremental(ctx, r.path, prefix, name):
            # incremental/composite (possibly inherited through `extends`)
            # writes *.tsbuildinfo even with --noEmit: send it outside the repo.
            # Only then: TS < 5.6 rejects the flag otherwise (TS5069).
            args = args + ["--tsBuildInfoFile", os.path.join(ctx.scratch("tsbuildinfo"), "%s.tsbuildinfo" % name)]
        if prefix and os.path.isabs(prefix[0]) and lexically_inside(ctx, prefix[0]):
            prefix = [rel_exe(prefix[0], ctx.abspath(r.path))] + prefix[1:]
        argv = (prefix or [binname]) + args
        r.checks.append(Check(name, r.path, argv, files_ok, exts, prefix is not None, src))


def _ts_incremental(ctx, root, prefix, name):
    """Is the effective tsconfig incremental or composite? Asks the compiler
    (`--showConfig` resolves `extends` and writes nothing); if that fails,
    reads the tsconfig chain through relative `extends` paths. Unknown -> False
    with a warning (no flag, so no false TS5069 failure)."""
    key = ("ts-incremental", tuple(prefix), root)
    if key in ctx.cache:
        return ctx.cache[key]
    code, out = capture(prefix + ["--showConfig", "-p", "tsconfig.json"], ctx.abspath(root), timeout=120)
    result = None
    if code == 0:
        try:
            opts = json.loads(out[out.index("{"):]).get("compilerOptions") or {}
            result = bool(opts.get("incremental") or opts.get("composite"))
        except ValueError:
            result = None
    if result is None:
        result = _ts_incremental_static(ctx.abspath(_join(root, "tsconfig.json")), 0)
    if result is None:
        ctx.warn("%s @ %s: could not resolve whether the tsconfig is incremental; if it is, a "
                 ".tsbuildinfo may be written into the repo" % (name, root))
        result = False
    ctx.cache[key] = result
    return result


def _jsonc(text):
    """tsconfig-style JSON: comments and trailing commas allowed."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            k = text.find("\n", i)
            i = n if k == -1 else k
        elif text.startswith("/*", i):
            k = text.find("*/", i + 2)
            i = n if k == -1 else k + 2
        else:
            out.append(c)
            i += 1
    try:
        return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))
    except ValueError:
        return None


def _ts_incremental_static(path, depth):
    """True/False from the file and its relative `extends` chain; None if a
    file is unreadable or an extends is a package specifier."""
    if depth > 16:
        return None
    data = _jsonc(read_text(path) or "")
    if not isinstance(data, dict):
        return None
    co = data.get("compilerOptions") or {}
    for k in ("incremental", "composite"):
        if k in co:
            if co[k]:
                return True
    own = {k: co[k] for k in ("incremental", "composite") if k in co}
    ext = data.get("extends")
    exts = [ext] if isinstance(ext, str) else (ext if isinstance(ext, list) else [])
    inherited = False
    for e in reversed(exts):  # later entries win
        if not isinstance(e, str) or not e.startswith((".", "/")):
            return None if not own else False
        p = os.path.normpath(os.path.join(os.path.dirname(path), e))
        if not p.endswith(".json") and not os.path.isfile(p):
            p += ".json"
        r = _ts_incremental_static(p, depth + 1)
        if r is None:
            return None if not own else False
        inherited = inherited or r
    if own:
        return False  # explicitly off in this file (overrides parents)
    return inherited


def _py_checks(ctx, r):
    proj = r.manifest
    tool = proj.get("tool") or {}
    deps = _py_deps(proj)
    base = ctx.abspath(r.path)
    specs = []
    ruff = "ruff" in tool or "ruff" in deps or _has_conf(ctx, r.path, ("ruff.toml", ".ruff.toml"))
    if ruff:
        # --no-fix overrides `fix = true` in config; --no-cache keeps .ruff_cache out of the repo
        specs.append(("ruff check", "ruff", ["check", "--no-fix", "--no-cache", "{files}"], True))
        specs.append(("ruff format", "ruff", ["format", "--check", "--no-cache", "{files}"], True))
    if "mypy" in tool or "mypy" in deps or _has_conf(ctx, r.path, ("mypy.ini", ".mypy.ini")) or _ini_has(os.path.join(base, "setup.cfg"), "mypy"):
        specs.append(("mypy", "mypy", ["--cache-dir", os.devnull, "{files}"], True))  # no .mypy_cache
    if "pyright" in tool or "pyright" in deps or "basedpyright" in deps or _has_conf(ctx, r.path, ("pyrightconfig.json",)):
        specs.append(("pyright", "pyright", ["{files}"], True))
    overrides = ctx.config.get("checks", {})
    lock_versions = _uv_lock_versions(ctx)
    for name, binname, args, files_ok in specs:
        if name in overrides:
            continue
        prefix, src = python_tool(ctx, r.path, binname)
        if prefix is None and binname == "ruff" and shutil.which("uvx"):
            ver = lock_versions.get("ruff")
            prefix, src = ["uvx", "ruff@" + ver if ver else "ruff"], "ephemeral:uvx"
        if prefix and os.path.isabs(prefix[0]) and lexically_inside(ctx, prefix[0]):
            prefix = [rel_exe(prefix[0], ctx.abspath(r.path))] + prefix[1:]
        argv = (prefix or [binname]) + args
        r.checks.append(Check(name, r.path, argv, files_ok, PY_EXTS, prefix is not None, src))


def _uv_lock_versions(ctx):
    if "uvlock" in ctx.cache:
        return ctx.cache["uvlock"]
    vers = {}
    text = read_text(os.path.join(ctx.repo, "uv.lock"), 32 << 20)
    if text:
        for m in re.finditer(r'\[\[package\]\]\s*\nname = "([^"]+)"\s*\nversion = "([^"]+)"', text):
            vers[m.group(1)] = m.group(2)
    ctx.cache["uvlock"] = vers
    return vers


def config_checks(ctx):
    """Checks declared in config: {name: template-string-or-null}."""
    import shlex

    out = []
    for name, cmd in sorted(ctx.config.get("checks", {}).items()):
        if cmd is None:
            continue
        argv = shlex.split(cmd)
        out.append(Check(name, ".", argv, any("{files}" in a or "{file}" in a for a in argv), None, True, "config"))
    return out


# --------------------------------------------------------------------------
# ownership


def roots_of(ctx, eco=None):
    return [r for r in detection(ctx)["roots"] if eco is None or r.ecosystem == eco]


def eco_of_path(path):
    low = path.lower()
    if low.endswith(PY_EXTS):
        return "python"
    if low.endswith(JS_EXTS + (".vue", ".svelte", ".astro")):
        return "js"
    return None


def nearest_root(ctx, path, eco):
    cands = [r for r in roots_of(ctx, eco) if under(path, r.path)]
    if not cands:
        return None
    return max(cands, key=lambda r: -1 if r.path == "." else len(r.path))


def test_root(ctx, path, eco):
    """Nearest root with its own runner signals (packages without any defer up)."""
    key = ("test_root", _dir(path), eco)
    if key not in ctx.cache:
        ctx.cache[key] = _test_root(ctx, path, eco)
    return ctx.cache[key]


def _test_root(ctx, path, eco):
    cands = [r for r in roots_of(ctx, eco) if under(path, r.path)]
    cands.sort(key=lambda r: (0 if r.path == "." else r.path.count("/") + 1), reverse=True)
    for r in cands:
        if r.own_runner_signals:
            return r
    return cands[0] if cands else None


def py_test_name(ctx, path):
    from . import pytestcfg

    return pytestcfg.is_test_file(ctx, path)


def analyzers(ctx):
    """Dead-code analyzer availability, without running anything."""
    out = []
    det = detection(ctx)
    dc = ctx.config.get("deadcode", {})
    disabled = set(dc.get("disable", []))
    js_roots = [r for r in det["roots"] if r.ecosystem == "js"]
    if js_roots and "knip" not in disabled:
        units = ["."] if any(r.path == "." for r in js_roots) else _outermost([r.path for r in js_roots])
        for u in units:
            r = next(x for x in js_roots if x.path == u)
            out.append(dict(knip_command(ctx, r), name="knip", root=u, ecosystem="js"))
    py_roots = [r for r in det["roots"] if r.ecosystem == "python"]
    for r in py_roots:
        uvx = shutil.which("uvx")
        for name, pkg_key in (("vulture", "vulturePackage"), ("deptry", "deptryPackage")):
            if name in disabled:
                continue
            spec = dc.get(pkg_key, name)
            out.append({
                "name": name, "root": r.path, "ecosystem": "python",
                "command": ["uvx", "--quiet", spec] if uvx else None,
                "source": "ephemeral:uvx" if uvx else "unavailable: uvx not found (install uv)",
                "available": bool(uvx),
            })
    return out


def knip_command(ctx, r):
    spec = ctx.config.get("deadcode", {}).get("knipPackage", "knip@6")
    prefix, src = node_tool(ctx, r.path, "knip", r.pm)
    if prefix:
        return {"command": prefix, "source": src, "available": True}
    pm = (r.pm or {}).get("name", "npm")
    yarn_berry = pm == "yarn" and (os.path.isfile(os.path.join(ctx.repo, ".yarnrc.yml")) or (r.pm.get("version") or "1").split(".")[0] not in ("0", "1"))
    order = {
        "pnpm": [("pnpm", ["pnpm", "dlx", spec])],
        "yarn": [("yarn", ["yarn", "dlx", spec])] if yarn_berry else [],
        "bun": [("bunx", ["bunx", spec])],
    }.get(pm, [])
    order += [("npx", ["npx", "--yes", spec])]
    for exe, argv in order:
        if shutil.which(exe):
            return {"command": argv, "source": "ephemeral:" + exe, "available": True}
    return {"command": None, "source": "unavailable: no node package runner (pnpm/npx/yarn/bunx) on PATH", "available": False}
