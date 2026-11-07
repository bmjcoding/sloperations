"""detect, run, changed, loc, baseline; deadcode/ledger/mutate live in their modules."""

import json
import os
import re
import shlex

from . import classify, detect, runners
from .classify import REPORT_CLASSES
from .core import (
    EXIT_OK, Outcome, UsageError, combine_exit, git, posix, resolve_base, run_process,
    shell_join, signal_exit, split_z, substitute,
)
from .deadcode import cmd_deadcode  # noqa: F401
from .ledger import cmd_ledger  # noqa: F401
from .mutate import cmd_mutate  # noqa: F401


# --------------------------------------------------------------------------
# detect


def cmd_detect(ctx, args):
    det = detect.detection(ctx)
    roots = []
    text = ["repo: %s" % ctx.repo]
    for r in det["roots"]:
        j = r.to_json()
        tpl = runners.template_for_root(ctx, r)
        j["test_command"] = {
            "runner": tpl.runner,
            "command": tpl.argv,
            "single_worker_flags": tpl.worker_flags,
            "flags_verified": tpl.flags_verified,
            "notes": tpl.notes,
            "error": tpl.error,
        }
        roots.append(j)
        pm = r.pm or {}
        text.append("root %s [%s]%s" % (r.path, r.ecosystem, " (%s)" % r.name if r.name else ""))
        text.append("  package manager: %s (%s)" % (pm.get("name"), pm.get("source")))
        if r.ambiguous:
            text.append("  runner: AMBIGUOUS (%s) -> resolved per file by nearest config, or set it in .test-audit.json"
                        % ", ".join(x["runner"] for x in r.runners_detected))
        else:
            text.append("  runner: %s (%s)" % (r.runner, r.runner_source))
        if tpl.argv:
            text.append("  test command: %s%s" % (shell_join(tpl.argv), "" if tpl.flags_verified else "  [flags unverified]"))
        elif tpl.error:
            text.append("  test command: unavailable: %s" % tpl.error)
        for c in r.checks:
            text.append("  check %-12s %s%s" % (c.name, shell_join(c.argv), "" if c.available else "  [unavailable: %s]" % c.source))
    analyzers = detect.analyzers(ctx)
    cfg_checks = [c.to_json() for c in detect.config_checks(ctx)]
    for c in cfg_checks:
        text.append("config check %s: %s" % (c["name"], shell_join(c["command"])))
    for a in analyzers:
        text.append("deadcode %s @ %s: %s" % (a["name"], a["root"], shell_join(a["command"]) if a["command"] else a["source"]))
    ecos = sorted({r.ecosystem for r in det["roots"]})
    text.insert(1, "ecosystems: %s" % (", ".join(ecos) or "none"))
    cfg = {"path": ctx.relpath(ctx.config_path) if ctx.config_path and ctx.inside(ctx.config_path) else ctx.config_path,
           "applied": getattr(ctx, "config_applied", [])}
    if cfg["path"]:
        text.append("config: %s (%s)" % (cfg["path"], ", ".join(cfg["applied"])))
    result = {
        "ecosystems": ecos,
        "roots": roots,
        "workspaces": det["workspaces"],
        "config_checks": cfg_checks,
        "deadcode_analyzers": analyzers,
        "config": cfg,
    }
    return Outcome(EXIT_OK, result, text)


# --------------------------------------------------------------------------
# run


def cmd_run(ctx, args):
    files = [ctx.resolve_user_path(f) for f in args.files]
    results = runners.run_files(ctx, files)  # plans all first: non-test files fail before anything runs
    exit_code = combine_exit(r["status"] for r in results)  # dry-run: 2 if a command cannot be built
    text = []
    if ctx.dry_run:
        for r in results:
            text.append("(cd %s && %s)" % (ctx.shown_cwd(r["root"]), shell_join(r["command"])) if r["command"] else "# %s: %s" % (r["file"], r["reason"]))
    else:
        text.append(_summary(results))
    return Outcome(exit_code, {"results": results, "summary": _counts(results)}, text)


def _counts(results):
    c = {"passed": 0, "failed": 0, "error": 0}
    for r in results:
        c[r["status"]] = c.get(r["status"], 0) + 1
    c["total"] = len(results)
    c["duration_s"] = round(sum(r["duration_s"] for r in results), 3)
    return c


def _summary(results):
    c = _counts(results)
    return "%d passed, %d failed, %d error (%.2fs)" % (c["passed"], c["failed"], c["error"], c["duration_s"])


# --------------------------------------------------------------------------
# changed


def changed_paths(ctx, base_sha):
    """Union of committed (base..HEAD), staged, unstaged and untracked paths.

    Returns (changed: {path: {"status", "sources", "net_change"}}, deleted:
    {path: sources}). A path whose staged and unstaged edits cancel out is
    still reported (net_change false): it was touched and its owners matter."""
    def names(*args):
        return split_z(git(ctx.repo, "diff", "--name-only", "-z", "--no-renames", *args, "--"))

    sources = {}
    for src, paths in (
        ("committed", names(base_sha, "HEAD")),
        ("staged", names("--cached")),
        ("unstaged", names()),
        ("untracked", split_z(git(ctx.repo, "ls-files", "-z", "--others", "--exclude-standard"))),
    ):
        for path in paths:
            sources.setdefault(path, []).append(src)
    net = {}
    toks = split_z(git(ctx.repo, "diff", "--name-status", "-z", "--no-renames", base_sha, "--"))
    for i in range(0, len(toks) - 1, 2):
        net[toks[i + 1]] = toks[i][0]
    for path in net:
        sources.setdefault(path, []).append("net")
    changed, deleted = {}, {}
    for path, srcs in sources.items():
        srcs = [x for x in srcs if x != "net"] or ["net"]
        if not os.path.lexists(ctx.abspath(path)):
            deleted[path] = srcs
            continue
        st = net.get(path)
        if "untracked" in srcs:
            status = "untracked"
        elif st == "A":
            status = "added"
        else:
            status = "modified"
        changed[path] = {"status": status, "sources": srcs, "net_change": st is not None or "untracked" in srcs}
    return changed, deleted


def _test_stem(path):
    base = os.path.basename(path)
    low = base
    if low.endswith(".py"):
        s = low[:-3]
        if s.startswith("test_"):
            return s[5:], "python"
        if s.endswith("_test"):
            return s[:-5], "python"
        return None, None
    for ext in sorted(detect.JS_EXTS, key=len, reverse=True):
        if low.endswith(ext):
            s = low[: -len(ext)]
            m = re.match(r"^(.*?)(\.(test|spec)(-d)?|_(test|spec))$", s)
            if m:
                return m.group(1), "js"
            if "/__tests__/" in "/" + path:
                return s, "js"
            return None, None
    return None, None


def _prod_stem(path):
    base = os.path.basename(path)
    if base.endswith(".pyi"):
        return base[:-4], "python"
    if base.endswith(".py"):
        return base[:-3], "python"
    for ext in sorted(detect.JS_EXTS + (".vue", ".svelte", ".astro"), key=len, reverse=True):
        if base.endswith(ext):
            return base[: -len(ext)], "js"
    return None, None


_TEST_DIRS = ("tests", "test", "__tests__", "spec", "specs")
_SRC_PREFIX = ("src", "lib", "source", "app")


def _suffix_match(a, b):
    a = [x for x in a if x]
    b = [x for x in b if x]
    n = min(len(a), len(b))
    return n == 0 or a[-n:] == b[-n:]


def owner_tests(ctx, prod, tests):
    """Map a production file to tests by naming convention. -> [(test, rule)]"""
    stem, eco = _prod_stem(prod)
    if not stem:
        return []
    pdir = prod.split("/")[:-1]
    out = []
    for t in tests:
        tstem, teco = _test_stem(t)
        if tstem != stem or teco != eco:
            continue
        tdir = t.split("/")[:-1]
        if tdir == pdir:
            out.append((t, "sibling"))
            continue
        idx = [i for i, seg in enumerate(tdir) if seg in _TEST_DIRS]
        if not idx:
            continue
        k = idx[-1] if tdir[idx[-1]] == "__tests__" else idx[0]
        prefix, trel = tdir[:k], tdir[k + 1 :]
        if pdir[: len(prefix)] != prefix:
            continue
        prel = pdir[len(prefix) :]
        if tdir[k] == "__tests__":
            if prel == trel:
                out.append((t, "__tests__"))
            continue
        while prel and prel[0] in _SRC_PREFIX:
            prel = prel[1:]
        if _suffix_match(prel, trel):
            out.append((t, "%s/ mirror" % "/".join(tdir[: k + 1])))
    return out


def _assign_checks(ctx, files):
    """[(Check, [files relative to check root])]"""
    det = detect.detection(ctx)
    assigned = {}
    order = []
    for f in files:
        eco = detect.eco_of_path(f)
        ext = os.path.splitext(f)[1].lower()
        for r_eco in ([eco] if eco else ["js", "python"]):
            cands = [r for r in det["roots"] if r.ecosystem == r_eco and detect.under(f, r.path)]
            cands.sort(key=lambda r: -1 if r.path == "." else len(r.path), reverse=True)
            seen = set()
            for r in cands:
                for c in r.checks:
                    if c.name in seen or not c.exts or ext not in c.exts:
                        continue
                    seen.add(c.name)
                    key = (c.name, r.path)
                    if key not in assigned:
                        assigned[key] = (c, [])
                        order.append(key)
                    assigned[key][1].append(posix(os.path.relpath(ctx.abspath(f), ctx.abspath(r.path))))
    out = [assigned[k] for k in sorted(order)]
    for c in detect.config_checks(ctx):
        if files:
            out.append((c, list(files)))
    return out


def _check_argv(c, files):
    """Same rules as test templates: whole-word placeholders become raw argv
    entries; embedded ones are shell-quoted, position-checked, one pass."""
    raw = {"file": files[:1], "files": list(files), "root": [c.root]}
    quoted = {"files": shell_join(files), "file": shlex.quote(files[0]) if files else "''", "root": shlex.quote(c.root)}
    argv = []
    for tok in c.argv:
        m = runners._TEMPLATE.fullmatch(tok)
        if m:
            argv += raw[m.group(1)]
        else:
            argv.append(substitute(tok, runners._TEMPLATE, quoted, "check %s" % c.name))
    return argv


def cmd_changed(ctx, args):
    base_sha, base_desc = resolve_base(ctx, args.base)
    changed, deleted = changed_paths(ctx, base_sha)
    rows = []
    for p in sorted(changed):
        cls = classify.classify(ctx, p)
        if cls == "ignore":
            continue
        rows.append(dict(changed[p], path=p, **{"class": cls}))
    deleted_rows = [{"path": p, "class": classify.classify(ctx, p), "sources": deleted[p]}
                    for p in sorted(deleted) if classify.classify(ctx, p) != "ignore"]
    all_tests = classify.test_files(ctx, discovery=False)  # owners may live outside testpaths
    owners, unmapped = {}, []
    for row in rows:
        if row["class"] == "production":
            o = owner_tests(ctx, row["path"], all_tests)
            if o:
                owners[row["path"]] = [{"test": t, "rule": rule} for t, rule in o]
            elif _prod_stem(row["path"])[0]:
                unmapped.append(row["path"])
    tests = {}
    for row in rows:
        if row["class"] == "test":
            tests.setdefault(row["path"], []).append("touched")
    for prod, lst in owners.items():
        for o in lst:
            tests.setdefault(o["test"], []).append("owner of " + prod)
    existing = [r["path"] for r in rows if r["class"] not in ("generated",)]
    check_plan = []
    for c, files in _assign_checks(ctx, existing):
        if not files:
            continue
        argv = _check_argv(c, files) if c.accepts_files else list(c.argv)
        check_plan.append({
            "name": c.name, "root": c.root, "command": ctx.wrap(argv, c.root), "files": files,
            "available": c.available, "source": c.source,
        })
    test_plan = []
    for t in sorted(tests):
        p = runners.plan_for(ctx, t)
        test_plan.append({
            "file": t, "reasons": tests[t], "root": p.cwd_rel, "runner": p.runner,
            "command": ctx.wrap(p.argv, p.cwd_rel) if p.argv else None, "error": p.error,
            "_plan": p,
        })
    result = {
        "base": base_sha, "base_ref": base_desc, "files": rows, "deleted": deleted_rows,
        "owners": owners, "unmapped_production": unmapped,
        "support_changed": [r["path"] for r in rows if r["class"] == "support"],
        "plan": {"checks": check_plan, "tests": [{k: v for k, v in t.items() if k != "_plan"} for t in test_plan]},
    }
    text = ["base: %s (%s)" % (base_sha[:12], base_desc)]
    for cls in REPORT_CLASSES:
        items = [r for r in rows if r["class"] == cls]
        if items:
            text.append("%s:" % cls)
            text += ["  %s [%s%s]" % (r["path"], r["status"], "" if r["net_change"] else ", net unchanged") for r in items]
    if deleted_rows:
        text.append("deleted:")
        text += ["  %s [%s]" % (r["path"], r["class"]) for r in deleted_rows]
    if unmapped:
        text.append("production without convention-mapped tests:")
        text += ["  " + u for u in unmapped]
    text.append("plan:")
    for c in check_plan:
        text.append("  check %s: (cd %s && %s)%s" % (c["name"], ctx.shown_cwd(c["root"]), shell_join(c["command"]), "" if c["available"] else "  [unavailable: %s]" % c["source"]))
    for t in test_plan:
        if t["command"]:
            text.append("  test %s: (cd %s && %s)  # %s" % (t["file"], ctx.shown_cwd(t["root"]), shell_join(t["command"]), "; ".join(t["reasons"])))
        else:
            text.append("  test %s: unavailable: %s" % (t["file"], t["error"]))
    if not check_plan and not test_plan:
        text.append("  (nothing to run)")
    if not args.run:
        return Outcome(EXIT_OK, result, text)

    # --run
    statuses = []
    check_results = []
    for c in check_plan:
        rec = {"name": c["name"], "root": c["root"], "command": c["command"]}
        if not c["available"]:
            rec.update(status="unavailable", reason=c["source"], exit_code=None, duration_s=0.0)
        elif ctx.dry_run:
            rec.update(status="dry-run", exit_code=None, duration_s=0.0, reason=None)
        else:
            stream = ctx.stream()
            if stream is not None:
                stream.write("$ (cd %s && %s)\n" % (ctx.shown_cwd(c["root"]), shell_join(c["command"])))
            cache_env, cache_dir = ctx.fresh_pycache()  # Python tools (mypy, ...) write no .pyc into the repo/.venv
            try:
                res = run_process(c["command"], ctx.exec_cwd(c["root"]), stream=stream, timeout=ctx.timeout, env=cache_env)
            finally:
                ctx.drop(cache_dir)
            if res.error in ("not-found", "not-executable") or res.exit_code in (126, 127):
                rec.update(status="unavailable", reason="command not found: %s" % c["command"][0])
            elif res.error == "timeout":
                rec.update(status="error", reason="timed out")
            elif signal_exit(res.exit_code):
                rec.update(status="error", reason=signal_exit(res.exit_code))
            elif ctx.via and res.exit_code == 255:
                rec.update(status="error", reason="remote transport failed (exit 255)")
            else:
                rec.update(status="passed" if res.exit_code == 0 else "failed", reason=None)
            rec.update(exit_code=res.exit_code, duration_s=res.duration)
            if ctx.json:
                rec["output_tail"] = res.tail(40)
            ctx.progress("check %s %s" % (c["name"], rec["status"].upper())) if ctx.json else None
        statuses.append(rec["status"])
        check_results.append(rec)
    test_results = []
    for i, t in enumerate(test_plan, 1):
        r = runners.execute(ctx, t["_plan"], label="[%d/%d]" % (i, len(test_plan)) if ctx.json else None)
        test_results.append(r)
        statuses.append(r["status"])
    result["results"] = {"checks": check_results, "tests": test_results}
    exit_code = combine_exit(statuses)
    text.append("results:")
    for c in check_results:
        text.append("  %s check %s%s" % (c["status"].upper(), c["name"], " (%s)" % c["reason"] if c.get("reason") else ""))
    for r in test_results:
        text.append("  %s %s %.2fs%s" % (r["status"].upper(), r["file"], r["duration_s"], " (%s)" % r["reason"] if r["reason"] else ""))
    return Outcome(exit_code, result, text)


# --------------------------------------------------------------------------
# loc


def loc_totals(ctx, scopes):
    """Absolute line counts per class for tracked + untracked files in scope
    in the working tree. Shared by `loc --totals` and `baseline` (.loc)."""
    by = {c: {"files": 0, "lines": 0} for c in REPORT_CLASSES}
    binary, ignored = [], []
    for f in ctx.files():
        if not in_scope(f, scopes):
            continue
        cls = classify.classify(ctx, f)
        if cls == "ignore":
            ignored.append(f)
            continue
        full = ctx.abspath(f)
        by[cls]["files"] += 1
        if classify.is_binary(full):
            binary.append(f)
        else:
            by[cls]["lines"] += classify.count_lines(full)
    totals = {k: sum(by[c][k] for c in REPORT_CLASSES) for k in ("files", "lines")}
    return by, totals, binary, ignored


def cmd_loc(ctx, args):
    scopes = scope_paths(ctx, args.paths)
    if args.totals:
        if args.base:
            raise UsageError("--totals counts the working tree; it takes no --base")
        by, totals, binary, ignored = loc_totals(ctx, scopes)
        result = {"mode": "totals", "scope": scopes or ["."], "by_class": by, "totals": totals,
                  "binary": binary, "ignored": ignored}
        text = ["scope: %s" % ", ".join(scopes or ["."]), "%-11s %6s %9s" % ("class", "files", "lines")]
        for c in REPORT_CLASSES:
            if by[c]["files"]:
                text.append("%-11s %6d %9d" % (c, by[c]["files"], by[c]["lines"]))
        text.append("%-11s %6d %9d" % ("total", totals["files"], totals["lines"]))
        if binary:
            text.append("binary (files counted, lines not): " + ", ".join(binary))
        return Outcome(EXIT_OK, result, text)
    base_sha, base_desc = resolve_base(ctx, args.base)
    out = git(ctx.repo, "diff", "--numstat", "-z", "--no-renames", base_sha, "--")
    files = []
    for tok in split_z(out):
        parts = tok.split("\t", 2)
        if len(parts) != 3:
            continue
        a, r, path = parts
        binary = a == "-"
        files.append({"path": path, "added": 0 if binary else int(a), "removed": 0 if binary else int(r), "binary": binary, "untracked": False})
    for p in split_z(git(ctx.repo, "ls-files", "-z", "--others", "--exclude-standard")):
        full = ctx.abspath(p)
        if not os.path.isfile(full):
            continue
        binary = classify.is_binary(full)
        files.append({"path": p, "added": 0 if binary else classify.count_lines(full), "removed": 0, "binary": binary, "untracked": True})
    by = {c: {"files": 0, "added": 0, "removed": 0, "net": 0} for c in REPORT_CLASSES}
    kept, ignored = [], []
    for f in sorted(files, key=lambda x: x["path"]):
        if not in_scope(f["path"], scopes):
            continue
        cls = classify.classify(ctx, f["path"])
        if cls == "ignore":
            ignored.append(f["path"])
            continue
        f["class"] = cls
        kept.append(f)
        b = by[cls]
        b["files"] += 1
        b["added"] += f["added"]
        b["removed"] += f["removed"]
        b["net"] = b["added"] - b["removed"]
    tot = {k: sum(by[c][k] for c in REPORT_CLASSES) for k in ("files", "added", "removed")}
    tot["net"] = tot["added"] - tot["removed"]
    binary = [f["path"] for f in kept if f["binary"]]
    result = {"mode": "diff", "scope": scopes or ["."], "base": base_sha, "base_ref": base_desc, "files": kept,
              "by_class": by, "totals": tot, "binary": binary, "ignored": ignored}
    text = ["base: %s (%s)" % (base_sha[:12], base_desc), "%-11s %6s %8s %8s %8s" % ("class", "files", "added", "removed", "net")]
    for c in REPORT_CLASSES:
        b = by[c]
        if b["files"]:
            text.append("%-11s %6d %8d %8d %+8d" % (c, b["files"], b["added"], b["removed"], b["net"]))
    text.append("%-11s %6d %8d %8d %+8d" % ("total", tot["files"], tot["added"], tot["removed"], tot["net"]))
    if binary:
        text.append("binary (not counted): " + ", ".join(binary))
    return Outcome(EXIT_OK, result, text)


# --------------------------------------------------------------------------
# baseline


def scope_paths(ctx, paths):
    scopes = []
    for p in paths or []:
        rel = ctx.resolve_user_path(p)
        scopes.append(rel)
    return scopes or None


def in_scope(path, scopes):
    return not scopes or any(s == "." or path == s or path.startswith(s.rstrip("/") + "/") for s in scopes)


def cmd_baseline(ctx, args):
    if not args.out and not ctx.json:
        ctx.json = True  # the baseline document goes to stdout
    sha = git(ctx.repo, "rev-parse", "HEAD").strip()
    status = git(ctx.repo, "status", "--porcelain", "--untracked-files=normal")
    dirty = bool(status.strip())
    if dirty and not args.allow_dirty:
        raise UsageError("working tree is dirty; commit or stash first, or pass --allow-dirty")
    scopes = scope_paths(ctx, args.paths)
    tests = classify.test_files(ctx, scopes)
    saved_json = ctx.json
    ctx.json = True  # capture runner output; progress goes to stderr
    try:
        plans = [runners.plan_for(ctx, t) for t in tests]
        results = []
        for i, p in enumerate(plans, 1):
            r = runners.execute(ctx, p, label="[%d/%d]" % (i, len(plans)))
            if r["status"] == "passed":
                r.pop("output_tail", None)
            results.append(r)
    finally:
        ctx.json = saved_json
    loc, loc_total, _, _ = loc_totals(ctx, scopes)  # same classes and shape as `loc --totals`
    failing = [{"file": r["file"], "status": r["status"], "reason": r["reason"]} for r in results if r["status"] in ("failed", "error")]
    doc = {
        "kind": "test-audit-baseline",
        "head": sha,
        "dirty": dirty,
        "scope": scopes or ["."],
        "files": results,
        "summary": _counts(results),
        "failing": failing,
        "failing_note": "Baseline failures are likely product bugs, not stale tests: reproduce and repair the owner; do not delete the test for failing.",
        "loc": loc,
        "loc_totals": loc_total,
    }
    exit_code = combine_exit(r["status"] for r in results)
    text = []
    if args.out:
        out_path = os.path.abspath(args.out)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        c = doc["summary"]
        text = ["baseline %s%s -> %s" % (sha[:12], " (dirty)" if dirty else "", args.out),
                "%d test files: %d passed, %d failed, %d error" % (c["total"], c["passed"], c["failed"], c["error"]),
                "loc: " + ", ".join("%s %d files / %d lines" % (k, v["files"], v["lines"]) for k, v in loc.items() if v["files"])]
        if failing:
            text.append("failing (likely product bugs, not stale tests):")
            text += ["  %s %s%s" % (x["status"].upper(), x["file"], " (%s)" % x["reason"] if x["reason"] else "") for x in failing]
        result = {k: v for k, v in doc.items() if k != "files"}
        result["out"] = args.out
        return Outcome(exit_code, result, text)
    return Outcome(exit_code, doc, text)
