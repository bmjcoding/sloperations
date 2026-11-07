"""Per-file test commands, verified single-worker flags, execution, and
pass / fail / error interpretation."""

import os
import re
import shlex
import shutil

from . import classify, detect
from .core import UsageError, capture, glob_match, posix, run_process, shell_join, signal_exit, strip_ansi, substitute

FAMILY = {"vitest": "vitest", "vp": "vitest", "jest": "jest", "bun": "bun", "pytest": "pytest"}


class Plan:
    def __init__(self, file):
        self.file = file
        self.root = None
        self.runner = None
        self.argv = None
        self.cwd_rel = "."
        self.worker_flags = []
        self.flags_verified = False
        self.resolved_by = None
        self.notes = []
        self.error = None  # cannot build a command (unavailable / ambiguous)
        self.error_exit_codes = ()
        self.config_path = None  # repo-relative config chosen by nearest-config resolution

    def to_json(self):
        return {
            "file": self.file,
            "root": self.cwd_rel,
            "runner": self.runner,
            "resolved_by": self.resolved_by,
            "command": self.argv,
            "single_worker_flags": self.worker_flags,
            "flags_verified": self.flags_verified,
            "notes": self.notes,
            "error": self.error,
        }


# --------------------------------------------------------------------------
# help probes (local, read-only, cached)


def _help(ctx, argv, cwd):
    key = ("help", tuple(argv), cwd)
    if key not in ctx.cache:
        code, out = capture(argv, cwd, timeout=120, env={"PYTHONDONTWRITEBYTECODE": "1"})
        ctx.cache[key] = (code, out)
    return ctx.cache[key]


def _rel_exe(path, cwd_abs):
    return detect.rel_exe(path, cwd_abs)


# --------------------------------------------------------------------------
# script parsing: reuse env assignments and --config from scripts.test


def _script_extras(script, runner):
    """(env_assignments, extra_args, note) from a package.json test script."""
    if not script:
        return [], [], None
    try:
        toks = shlex.split(script)
    except ValueError:
        return [], [], "scripts.test not parsed (unbalanced quotes)"
    if any(t in ("&&", "||", ";", "|") for t in toks):
        return [], [], "scripts.test is a compound command; only the runner itself is used"
    env, i = [], 0
    if toks[:1] == ["cross-env"]:
        i = 1
    while i < len(toks) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[i]):
        env.append(toks[i])
        i += 1
    rest = toks[i:]
    names = {"vitest": ("vitest",), "vp": ("vp",), "jest": ("jest",)}.get(runner, ())
    if not rest or os.path.basename(rest[0]) not in names:
        return env, [], None
    extra = []
    j = 1
    while j < len(rest):
        t = rest[j]
        if t in ("--config", "-c") and j + 1 < len(rest):
            extra += ["--config", rest[j + 1]]
            j += 2
            continue
        if t.startswith("--config="):
            extra.append(t)
        j += 1
    return env, extra, None


# --------------------------------------------------------------------------
# runner resolution


def _nearest_config_runner(ctx, file, root):
    d = os.path.dirname(file) or "."
    while True:
        found = set()
        for stem, runner in (("jest.config", "jest"), ("vitest.config", "vitest"), ("vitest.workspace", "vitest")):
            for ext in detect.CONF_EXTS:
                p = ctx.abspath(detect._join(d, "%s.%s" % (stem, ext)))
                if os.path.isfile(p):
                    found.add((runner, detect._join(d, "%s.%s" % (stem, ext))))
        for ext in detect.CONF_EXTS:
            rel = detect._join(d, "vite.config." + ext)
            text = detect.read_text(ctx.abspath(rel))
            if text:
                if re.search(r"""from\s+['"]vite-plus['"]""", text):
                    found.add(("vp", rel))
                elif re.search(r"\btest\s*:", text):
                    found.add(("vitest", rel))
        bunfig = detect._join(d, "bunfig.toml")
        if re.search(r"^\s*\[test\]", detect.read_text(ctx.abspath(bunfig)) or "", re.M):
            found.add(("bun", bunfig))
        fams = {FAMILY[r] for r, _ in found}
        if len(fams) == 1:
            runner = "vp" if any(r == "vp" for r, _ in found) else sorted(found)[0][0]
            conf = sorted(p for r, p in found if r == runner)[0]
            return runner, "nearest-config:" + conf, conf
        if len(fams) > 1:
            return None, "conflicting configs in %s: %s" % (d, ", ".join(sorted(p for _, p in found))), None
        if d == root.path or d == ".":
            return None, None, None
        d = os.path.dirname(d) or "."


def plan_for(ctx, file, require_test=True):
    p = Plan(file)
    if require_test:
        cls = classify.classify(ctx, file)
        if cls != "test":
            raise UsageError("not a test file (classified %s): %s" % (cls, file))
    eco = detect.eco_of_path(file)
    for spec in ctx.config.get("runners", []):
        if glob_match(file, [spec["glob"]]):
            root = detect.test_root(ctx, file, eco) if eco else None
            p.root = root
            p.cwd_rel = root.path if root else "."
            p.resolved_by = "config:runners[%s]" % spec["glob"]
            if spec.get("testCommand"):
                return _custom(ctx, p, spec["testCommand"])
            return _build(ctx, p, spec["runner"])
    if eco is None:
        p.error = "no known test ecosystem for %s" % file
        return p
    root = detect.test_root(ctx, file, eco)
    if root is None:
        p.error = "no %s package root owns %s" % (eco, file)
        return p
    p.root = root
    p.cwd_rel = root.path
    ov = root.overrides
    if ov.get("testCommand"):
        p.resolved_by = "config:roots.%s.testCommand" % root.path
        return _custom(ctx, p, ov["testCommand"])
    if ov.get("runner"):
        p.resolved_by = "config:roots.%s.runner" % root.path
        return _build(ctx, p, ov["runner"])
    if ctx.config.get("testCommand"):
        p.resolved_by = "config:testCommand"
        return _custom(ctx, p, ctx.config["testCommand"])
    if root.ambiguous:
        runner, why, conf = _nearest_config_runner(ctx, file, root)
        if runner:
            p.resolved_by = why
            if runner != "bun":
                p.config_path = conf  # the selected config must also be the one the runner loads
            return _build(ctx, p, runner)
        names = ", ".join(sorted({x["runner"] for x in root.runners_detected}))
        p.error = (
            "ambiguous test runner for root %s (%s)%s; set roots[\"%s\"].runner or a runners glob in .test-audit.json"
            % (root.path, names, "; " + why if why else "", root.path)
        )
        return p
    if not root.runner:
        p.error = "no test runner detected for root %s" % root.path
        return p
    p.resolved_by = "detected:" + (root.runner_source or "signals")
    return _build(ctx, p, root.runner)


def _custom(ctx, p, template):
    p.runner = "custom"
    p.flags_verified = False
    p.notes.append("custom testCommand: pass/fail from exit code only")
    p.error_exit_codes = tuple(ctx.config.get("testErrorExitCodes", []))
    target = _target_rel(ctx, p)
    p.argv = expand_template(template, [target], p.cwd_rel)
    return p


_TEMPLATE = re.compile(r"\{(files|file|root)\}")


def expand_template(template, files, root_rel, where="test command template"):
    """Split once with shell rules, then per word:
    - a word that is exactly {files}/{file}/{root} becomes raw argv entries
      (no shell ever sees them);
    - placeholders embedded in a larger word (e.g. a `sh -c '...'` script)
      get shell-quoted values, are checked to be in unquoted, unescaped
      position, and are expanded in one pass (inserted text is never
      rescanned)."""
    try:
        words = shlex.split(template)
    except ValueError as e:
        raise UsageError("%s is not a valid command (%s): %r" % (where, e, template)) from None
    if not words:
        raise UsageError("%s is empty" % where)
    raw = {"file": files[:1], "files": list(files), "root": [root_rel]}
    def q(v):  # `detect` renders templates with a literal "{file}" value
        return v if v == "{file}" else shlex.quote(v)

    quoted = {"files": " ".join(q(f) for f in files), "file": q(files[0]) if files else "''", "root": q(root_rel)}
    out = []
    for tok in words:
        m = _TEMPLATE.fullmatch(tok)
        if m:
            out += raw[m.group(1)]
            continue
        out.append(substitute(tok, _TEMPLATE, quoted, where))
    return out


def _target_rel(ctx, p):
    cwd_abs = ctx.abspath(p.cwd_rel)
    return posix(os.path.relpath(ctx.abspath(p.file), cwd_abs))


def _build(ctx, p, runner):
    p.runner = runner
    root = p.root
    cwd_abs = ctx.abspath(p.cwd_rel)
    rel = _target_rel(ctx, p) if p.file else "{file}"
    script = root.test_script if root and root.ecosystem == "js" else None
    if runner in ("vitest", "vp", "jest"):
        env, extra, note = _script_extras(script if detect._script_runner(script) == runner else None, runner)
        if note:
            p.notes.append(note)
        if p.config_path:
            extra = ["--config", posix(os.path.relpath(ctx.abspath(p.config_path), cwd_abs))]
    else:
        env, extra = [], []
    if runner in ("vitest", "vp"):
        name = "vitest" if runner == "vitest" else "vp"
        b = detect.find_node_bin(ctx, p.cwd_rel, name)
        if b:
            exe = [_rel_exe(b, cwd_abs)]
            probe_exe = [b]
        elif runner == "vp" and shutil.which("vp"):
            exe = probe_exe = ["vp"]
            p.notes.append("using global vp (delegates to the project's vite-plus)")
        elif root and root.pm and root.pm.get("name") == "yarn" and detect.yarn_pnp(ctx):
            exe = probe_exe = ["yarn", "run", "--binaries-only", name]
        else:
            p.error = "%s is not installed (no node_modules/.bin/%s); install project deps first" % (name, name)
            return p
        sub = ["test", "run"] if runner == "vp" else ["run"]
        if p.file and not ctx.via:
            target = os.path.realpath(ctx.abspath(p.file))
        else:
            target = rel
            if p.file:
                p.notes.append("vitest filters are substring matches; relative path used under --via")
        code, help_text = _help(ctx, probe_exe + sub[:-1] + ["--help"] if runner == "vp" else probe_exe + ["--help"], cwd_abs)
        flags = []
        if code == 0 and help_text:
            p.flags_verified = True
            if "--maxWorkers" in help_text:
                flags.append("--maxWorkers=1")
                if "--minWorkers" in help_text:
                    flags.append("--minWorkers=1")
            elif "--fileParallelism" in help_text or "--no-file-parallelism" in help_text:
                flags.append("--no-file-parallelism")
            elif "--threads" in help_text:
                flags.append("--threads=false")
            else:
                p.flags_verified = False
                p.notes.append("no known single-worker flag in --help output")
        else:
            flags.append("--maxWorkers=1")
            p.notes.append("could not run `%s --help`; single-worker flag not verified" % name)
        p.worker_flags = flags
        no_write = []
        if code == 0 and help_text:
            # results cache and transformed-module cache would land in the repo
            if re.search(r"^\s*--cache\b", help_text, re.M):
                no_write.append("--no-cache")
            if "--fsModuleCache" in help_text:
                no_write.append("--no-fsModuleCache")
        p.argv = (["env"] + env if env else []) + exe + sub + [target] + flags + no_write + extra
        return p
    if runner == "jest":
        b = detect.find_node_bin(ctx, p.cwd_rel, "jest")
        if b:
            exe, probe_exe = [_rel_exe(b, cwd_abs)], [b]
        elif root and root.pm and root.pm.get("name") == "yarn" and detect.yarn_pnp(ctx):
            exe = probe_exe = ["yarn", "run", "--binaries-only", "jest"]
        else:
            p.error = "jest is not installed (no node_modules/.bin/jest); install project deps first"
            return p
        code, help_text = _help(ctx, probe_exe + ["--help"], cwd_abs)
        if code == 0 and "--runInBand" in help_text:
            flags, p.flags_verified = ["--runInBand"], True
        elif code == 0 and "--maxWorkers" in help_text:
            flags, p.flags_verified = ["--maxWorkers=1"], True
        else:
            flags = ["--runInBand"]
            p.notes.append("could not verify jest flags from --help")
        p.worker_flags = flags
        cache = [] if ctx.via else ["--cacheDirectory", ctx.scratch("jest-cache")]
        p.argv = (["env"] + env if env else []) + exe + flags + cache + ["--runTestsByPath", rel] + extra
        return p
    if runner == "bun":
        if not shutil.which("bun") and not ctx.via:
            p.error = "bun is not installed or not on PATH"
            return p
        p.flags_verified = True
        p.notes.append("bun test runs the given file in one process; no worker flag needed")
        p.argv = ["bun", "test", rel if rel.startswith(("./", "../", "/")) else "./" + rel]
        return p
    if runner == "pytest":
        py, src = detect.python_interpreter(ctx, p.cwd_rel)
        if not py:
            p.error = "no Python interpreter for root %s; create the project environment (e.g. `uv sync`) first" % p.cwd_rel
            return p
        exe = _rel_exe(py, cwd_abs) if detect.lexically_inside(ctx, py) else py
        p.notes.append("python from %s" % src)
        code, help_text = _help(ctx, [py, "-m", "pytest", "-p", "no:cacheprovider", "--help"], cwd_abs)
        flags = []
        if code == 0:
            p.flags_verified = True
            if "--numprocesses" in help_text:
                flags = ["-n", "0"]
                p.notes.append("pytest-xdist active: -n 0 disables workers")
        elif "No module named pytest" in help_text:
            if not ctx.via:
                p.error = "pytest is not installed in %s; create the project environment (e.g. `uv sync`) first" % (exe,)
                return p
            p.notes.append("pytest not importable locally; flags unverified")
        else:
            p.notes.append("`pytest --help` failed locally; xdist detection skipped")
        p.worker_flags = flags
        p.argv = ["env", "PYTHONDONTWRITEBYTECODE=1", exe, "-m", "pytest", rel, "-p", "no:cacheprovider"] + flags
        return p
    p.error = "unknown runner %s" % runner
    return p


def template_for_root(ctx, root):
    """Command template for `detect`: the plan with a `{file}` placeholder."""
    p = Plan(None)
    p.root = root
    p.cwd_rel = root.path
    if root.overrides.get("testCommand"):
        p.runner = "custom"
        p.argv = expand_template(root.overrides["testCommand"], ["{file}"], root.path)
        return p
    if ctx.config.get("testCommand") and not root.overrides.get("runner"):
        p.runner = "custom"
        p.argv = expand_template(ctx.config["testCommand"], ["{file}"], root.path)
        return p
    if not root.runner:
        p.error = "ambiguous: resolved per file (nearest config) or via config" if root.ambiguous else "no runner"
        return p
    return _build(ctx, p, root.runner)


# --------------------------------------------------------------------------
# execution and interpretation


def _int(m):
    return int(m.group(1)) if m else 0


_NO_SUMMARY = "runner exited %s without a test summary"


def interpret(runner, res, error_codes=(), via=False):
    """Return (status, reason). status: passed | failed | error.

    Signal deaths: a negative exit (killed, as seen by the kernel) is always
    an error. A positive 128+N exit is ambiguous (a shell reporting a killed
    child, or a runner configured to exit 130 on failure, e.g. Jest
    `testFailureExitCode`), so the runner's own result decides first and the
    128+N heuristic applies only when there is no framework result."""
    if res.exit_code is not None and res.exit_code < 0:
        return "error", signal_exit(res.exit_code)
    status, reason = _interpret(runner, res, error_codes, via)
    fam = FAMILY.get(runner, runner)
    no_result = (
        (status == "error" and reason == _NO_SUMMARY % res.exit_code)
        or (fam == "pytest" and res.exit_code not in (0, 1, 2, 3, 4, 5))
        or (fam not in FAMILY.values() and status == "failed")
    )
    if no_result and signal_exit(res.exit_code):
        return "error", signal_exit(res.exit_code)
    return status, reason


def _interpret(runner, res, error_codes=(), via=False):
    if res.error == "not-found":
        return "error", "command not found: %s" % res.argv[0]
    if res.error == "not-executable":
        return "error", "not executable: %s" % res.argv[0]
    if res.error == "timeout":
        return "error", "timed out"
    out = strip_ansi(res.output)
    code = res.exit_code
    if code in (126, 127):
        return "error", "command not found or not executable (exit %d)" % code
    if via and code == 255:
        return "error", "remote transport failed (exit 255)"
    fam = FAMILY.get(runner, runner)
    if fam == "pytest":
        if code == 0:
            return "passed", None
        if code == 1:
            return "failed", "tests failed"
        if code == 5:
            return "error", "no tests collected"
        if "No module named pytest" in out:
            return "error", "pytest not installed"
        return "error", {2: "collection error or interrupted", 3: "internal error", 4: "usage error"}.get(code, "exit %s" % code)
    if fam == "vitest":
        files = re.search(r"Test Files\s+(.*)\((\d+)\)", out)
        nfiles = int(files.group(2)) if files else None
        if "No test files found" in out:
            return "error", "no test files found (filter or include mismatch)"
        if nfiles is not None and nfiles > 1:
            return "error", "filter matched %d files; use a testCommand override" % nfiles
        if code == 0:
            return "passed", None
        tests_line = re.search(r"^\s*Tests\s+(.*)$", out, re.M)
        if tests_line and re.search(r"\d+ failed", tests_line.group(1)):
            return "failed", "tests failed"
        if re.search(r"^\s*Errors\s+\d+ errors?", out, re.M) and tests_line and "no tests" not in tests_line.group(1):
            return "failed", "unhandled errors during the run"
        if files and "failed" in files.group(1):
            return "error", "test file failed to load (collection error)"
        return "error", _NO_SUMMARY % code
    if fam == "jest":
        if code == 0:
            return "passed", None
        if "No tests found" in out:
            return "error", "no tests found"
        tests_line = re.search(r"^Tests:\s+(.*)$", out, re.M)
        if tests_line and re.search(r"\d+ failed", tests_line.group(1)):
            return "failed", "tests failed"
        if "Test suite failed to run" in out or (tests_line and "0 total" in tests_line.group(1)):
            return "error", "test suite failed to run (collection error)"
        return "error", _NO_SUMMARY % code
    if fam == "bun":
        passed = _int(re.search(r"^\s*(\d+) pass\b", out, re.M))
        failed_tests = len(re.findall(r"^\(fail\) ", out, re.M))
        errors = _int(re.search(r"^\s*(\d+) errors?\b", out, re.M))
        across = re.search(r"across (\d+) files", out)
        if across and int(across.group(1)) > 1:
            return "error", "filter matched %s files; use a testCommand override" % across.group(1)
        if code == 0:
            return "passed", None
        if failed_tests:
            return "failed", "tests failed"
        if errors or (passed == 0 and "Ran 0 tests" in out):
            return "error", "test file failed to load (collection error)"
        if re.search(r"^\s*(\d+) fail\b", out, re.M) and _int(re.search(r"^\s*(\d+) fail\b", out, re.M)):
            return "failed", "tests failed"
        return "error", _NO_SUMMARY % code
    # custom command
    if code == 0:
        return "passed", None
    if code in error_codes:
        return "error", "exit %d (configured as error)" % code
    return "failed", "exit %d" % code


def _vite_temp_snapshot(ctx, plan):
    """Vite bundles a TS config to node_modules/.vite-temp/<name>.timestamp-
    <ms>-<hash>.mjs and deletes it after loading; the directory stays.
    Nothing can redirect it, and a file name or timestamp does not prove
    which process wrote a file in that shared directory, so test-audit never
    deletes files there. It only removes the directory itself when this run
    created it and it is empty. Returns the dirs that did not exist yet."""
    if FAMILY.get(plan.runner) != "vitest" or ctx.via:
        return []
    out = []
    d = plan.cwd_rel
    while True:
        nm = os.path.join(ctx.abspath(d), "node_modules")
        if os.path.isdir(nm) and not os.path.lexists(os.path.join(nm, ".vite-temp")):
            out.append(os.path.join(nm, ".vite-temp"))
        if d == ".":
            return out
        d = os.path.dirname(d) or "."


def _vite_temp_cleanup(ctx, created):
    for vt in created:
        try:
            os.rmdir(vt)  # only succeeds when empty: never deletes a file
        except FileNotFoundError:
            pass
        except OSError:
            ctx.warn("Vite left its compiled config in %s (an interrupted config load does this); "
                     "it is inside the install directory and was not deleted" % ctx.relpath(vt))


def execute(ctx, plan, label=None, env=None):
    if plan.error:
        return {
            "file": plan.file, "root": plan.cwd_rel, "runner": plan.runner, "command": None,
            "status": "error", "exit_code": None, "duration_s": 0.0, "reason": plan.error,
        }
    argv = ctx.wrap(plan.argv, plan.cwd_rel)
    rec = {
        "file": plan.file, "root": plan.cwd_rel, "cwd": ctx.shown_cwd(plan.cwd_rel), "runner": plan.runner, "command": argv,
        "status": None, "exit_code": None, "duration_s": 0.0, "reason": None,
    }
    if ctx.dry_run:
        rec["status"] = "dry-run"
        return rec
    stream = ctx.stream()
    if stream is not None:
        stream.write("$ (cd %s && %s)\n" % (ctx.shown_cwd(plan.cwd_rel), shell_join(argv)))
        stream.flush()
    # Every run gets a fresh, external bytecode cache: no stale .pyc can be
    # read and none is written into the repo (applies to any Python child).
    cache_env, cache_dir = ctx.fresh_pycache()
    run_env = dict(cache_env)
    run_env.update(env or {})
    vite_temp = _vite_temp_snapshot(ctx, plan)
    try:
        res = run_process(argv, ctx.exec_cwd(plan.cwd_rel), stream=stream, timeout=ctx.timeout, env=run_env)
    finally:
        # runs on the interrupt path too, after the process group is gone
        ctx.drop(cache_dir)
        _vite_temp_cleanup(ctx, vite_temp)
    status, reason = interpret(plan.runner, res, plan.error_exit_codes, via=bool(ctx.via))
    if res.error == "timeout":
        reason = "timed out after %ss" % ctx.timeout
    rec.update(status=status, exit_code=res.exit_code, duration_s=res.duration, reason=reason)
    if ctx.json:
        rec["output_tail"] = res.tail(40)
    if label:
        ctx.progress("%s %s %s %.2fs%s" % (label, status.upper(), plan.file, res.duration, " (%s)" % reason if reason else ""))
    return rec


def run_files(ctx, files, require_test=True):
    plans = [plan_for(ctx, f, require_test=require_test) for f in files]
    results = []
    n = len(plans)
    for i, p in enumerate(plans, 1):
        label = "[%d/%d]" % (i, n) if (ctx.json or not ctx.dry_run) else None
        results.append(execute(ctx, p, label=label if ctx.json else None))
        if not ctx.json and not ctx.dry_run:
            r = results[-1]
            ctx.out.write("%s %s %.2fs%s\n" % (r["status"].upper(), r["file"], r["duration_s"], " (%s)" % r["reason"] if r["reason"] else ""))
            ctx.out.flush()
    return results
