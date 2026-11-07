"""deadcode: knip (JS), vulture + deptry (Python); new-since-base by default."""

import json
import os
import re
import shutil
from contextlib import contextmanager

from . import detect
from .core import (
    EXIT_OK, Outcome, combine_exit, git, posix, resolve_base, run_process,
    shell_join, strip_ansi,
)

VULTURE_WHITELISTS = ("whitelist.py", "vulture_whitelist.py", ".vulture_whitelist.py", "whitelist_vulture.py")
VULTURE_EXCLUDE = ".venv,venv,node_modules,.tox,.nox,build,dist,.git,__pycache__"
_NETWORK = re.compile(r"ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ETIMEDOUT|ERR_PNPM_FETCH|ERR_PNPM_META_FETCH|getaddrinfo|Failed to fetch|failed to download|dns error|Could not connect|network", re.I)


# --------------------------------------------------------------------------
# parsing


def parse_knip(text):
    i = text.find("{")
    while i != -1:
        try:
            data, _ = json.JSONDecoder().raw_decode(text[i:])
            if isinstance(data, dict) and ("issues" in data or "files" in data):
                break
        except ValueError:
            pass
        i = text.find("{", i + 1)
    else:
        return None
    findings = []
    for f in data.get("files") or []:  # knip 5: top-level unused files
        if isinstance(f, str):
            findings.append({"type": "files", "file": f, "name": f})
    for issue in data.get("issues") or []:
        file = issue.get("file")
        for typ, val in sorted(issue.items()):
            if typ in ("file", "owners"):
                continue
            for name, line in _knip_items(val):
                findings.append({"type": typ, "file": file, "name": name or file, "line": line})
    return findings


def _knip_items(val):
    if val is True:
        yield None, None
    elif isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                yield item.get("name") or item.get("symbol"), item.get("line")
            elif isinstance(item, list):  # duplicates: groups of dicts
                yield "|".join(sorted(str(x.get("name")) for x in item if isinstance(x, dict))), None
            elif isinstance(item, str):
                yield item, None
    elif isinstance(val, dict):  # knip 5 enumMembers/classMembers: {Parent: [items]}
        for parent, items in sorted(val.items()):
            for name, line in _knip_items(items):
                yield "%s.%s" % (parent, name), line


_VULTURE = re.compile(r"^(?P<file>.+?):(?P<line>\d+): (?P<msg>.+?) \((?P<conf>\d+)% confidence(?:, \d+ lines?)?\)\s*$")


def vulture_diagnostics(text):
    """Output lines that are not findings (syntax errors, decode errors,
    warnings). vulture can exit 3 ("dead code found") while having skipped
    an unparsable file, so any such line makes the run a failure."""
    return [ln for ln in text.splitlines() if ln.strip() and not _VULTURE.match(ln.strip())]


def parse_vulture(text):
    out = []
    for line in text.splitlines():
        m = _VULTURE.match(line.strip())
        if m:
            f = m.group("file")
            f = f[2:] if f.startswith("./") else f
            out.append({"type": m.group("msg").split(" '")[0], "file": posix(f), "name": m.group("msg"),
                        "line": int(m.group("line")), "confidence": int(m.group("conf"))})
    return out


def parse_deptry(data):
    out = []
    for v in data or []:
        err = v.get("error") or {}
        loc = v.get("location") or {}
        f = loc.get("file") or ""
        f = f[2:] if f.startswith("./") else f
        out.append({"type": err.get("code"), "file": posix(f), "name": "%s %s" % (err.get("code"), v.get("module")),
                    "line": loc.get("line"), "message": err.get("message")})
    return out


# --------------------------------------------------------------------------
# running


def _vulture_args(ctx, root_rel, tree):
    dc = ctx.config.get("deadcode", {})
    base = os.path.join(tree, *([] if root_rel == "." else root_rel.split("/")))
    proj = detect.read_toml(ctx, os.path.join(base, "pyproject.toml")) if os.path.isfile(os.path.join(base, "pyproject.toml")) else {}
    vcfg = ((proj or {}).get("tool") or {}).get("vulture")
    args = []
    configured = vcfg.get("paths") if isinstance(vcfg, dict) else None
    configured = [configured] if isinstance(configured, str) else list(configured or [])
    wl = dc.get("vultureWhitelist")
    if wl is None:
        wl = [w for w in VULTURE_WHITELISTS if os.path.isfile(os.path.join(base, w))]
    extra = [w for w in wl if w not in configured]
    if configured:
        # any positional argument replaces [tool.vulture].paths, so when a
        # whitelist is added the configured paths are passed explicitly too
        if extra:
            args += configured + extra
    else:
        args += ["."]
        if not (isinstance(vcfg, dict) and vcfg.get("exclude")):
            args += ["--exclude", VULTURE_EXCLUDE]
        args += extra
    if "vultureMinConfidence" in dc:
        args += ["--min-confidence", str(dc["vultureMinConfidence"])]
    return args


def analyzer_argv(ctx, a, tree, tmpdir):
    if a["name"] == "knip":
        return a["command"] + ["--reporter", "json", "--no-progress", "--no-exit-code"]
    if a["name"] == "vulture":
        return a["command"] + _vulture_args(ctx, a["root"], tree)
    if a["name"] == "deptry":
        return a["command"] + [".", "--no-ansi", "--json-output", os.path.join(tmpdir, "deptry.json")]
    raise ValueError(a["name"])


def run_analyzer(ctx, a, tree, label):
    rec = {"name": a["name"], "root": a["root"], "tree": label, "status": None, "reason": None, "findings": []}
    if not a["available"]:
        rec.update(status="unavailable", reason=a["source"])
        return rec
    cwd = os.path.join(tree, *([] if a["root"] == "." else a["root"].split("/")))
    if label == "base" and not ctx.dry_run:
        if not os.path.isdir(cwd):
            # the directory is new in this change: nothing existed at base
            rec.update(status="ok", reason="root directory absent at base", findings=[])
            return rec
        # vulture scans any existing directory. knip and deptry read their
        # inputs from a manifest; without it at base there is nothing to
        # compare, so the base set is empty (and said so)
        need = {"knip": "package.json", "deptry": "pyproject.toml"}.get(a["name"])
        if need and not os.path.isfile(os.path.join(cwd, need)):
            rec.update(status="ok", reason="no %s at base" % need, findings=[])
            return rec
    tmpdir = ctx.mkdtemp("test-audit-dc-", register=False)
    try:
        argv = analyzer_argv(ctx, a, tree, tmpdir)
        rec["command"] = argv
        if ctx.dry_run:
            rec["status"] = "dry-run"
            return rec
        ctx.progress("deadcode %s @ %s (%s): %s" % (a["name"], a["root"], label, shell_join(argv)))
        res = run_process(argv, cwd, timeout=ctx.timeout or 900)
        out = strip_ansi(res.output)
        rec["exit_code"] = res.exit_code
        if res.error:
            rec.update(status="unavailable" if res.error == "not-found" else "error", reason=res.error, output_tail=res.tail(20))
            return rec
        findings = None
        if a["name"] == "knip":
            findings = parse_knip(out) if res.exit_code == 0 else None
        elif a["name"] == "vulture":
            # 0 = clean, 3 = dead code found; 1 (invalid input, e.g. a syntax
            # error) and 2 (bad arguments) are failures even with partial output
            findings = parse_vulture(out) if res.exit_code in (0, 3) else None
            diags = vulture_diagnostics(out)
            if findings is not None and diags:
                rec.update(status="error", reason="vulture could not analyze every file (%d diagnostic line(s))" % len(diags),
                           diagnostics=diags[:50], output_tail=res.tail(20))
                return rec
        elif a["name"] == "deptry":
            path = os.path.join(tmpdir, "deptry.json")
            if res.exit_code in (0, 1) and os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    findings = parse_deptry(json.load(f))
        if findings is None:
            net = bool(_NETWORK.search(out))
            rec.update(status="unavailable" if net else "error",
                       reason="analyzer could not run%s (exit %s)" % (" (network?)" if net else "", res.exit_code),
                       output_tail=res.tail(20))
            return rec
        for f in findings:
            f["analyzer"] = a["name"]
            f["root"] = a["root"]
        rec["findings"] = sorted(findings, key=lambda f: (f["file"] or "", str(f["type"]), str(f["name"])))
        rec["status"] = "ok"
        return rec
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def finding_key(f):
    return (f["analyzer"], f["root"], str(f["type"]), f["file"] or "", str(f["name"]))


@contextmanager
def base_worktree(ctx, sha):
    """Detached worktree at sha in a temp dir outside the repo; always removed.

    Head's installed node_modules are symlinked into the base tree so knip
    (and its plugins) resolve without installing anything."""
    tmp = ctx.mkdtemp("test-audit-base-", register=False)  # temp parent checked before creating
    wt = os.path.join(tmp, "tree")
    links = []
    added = False
    try:
        git(ctx.repo, "worktree", "add", "--detach", "--quiet", wt, sha)
        added = True
        dirs = {"."} | {r.path for r in detect.roots_of(ctx, "js")}
        for d in sorted(dirs):
            parts = [] if d == "." else d.split("/")
            src = os.path.join(ctx.repo, *(parts + ["node_modules"]))
            dst_dir = os.path.join(wt, *parts)
            dst = os.path.join(dst_dir, "node_modules")
            if os.path.isdir(src) and os.path.isdir(dst_dir) and not os.path.lexists(dst):
                os.symlink(src, dst)
                links.append(dst)
        for name in (".pnp.cjs", ".pnp.loader.mjs", ".yarn"):
            src = os.path.join(ctx.repo, name)
            dst = os.path.join(wt, name)
            if os.path.exists(src) and not os.path.lexists(dst):
                os.symlink(src, dst)
                links.append(dst)
        yield wt
    finally:
        for link in links:
            try:
                os.unlink(link)
            except OSError:
                pass
        if added:
            git(ctx.repo, "worktree", "remove", "--force", wt, check=False)
        shutil.rmtree(tmp, ignore_errors=True)
        git(ctx.repo, "worktree", "prune", check=False)


def cmd_deadcode(ctx, args):
    analyzers = detect.analyzers(ctx)
    if not analyzers:
        return Outcome(EXIT_OK, {"mode": "all" if args.all else "new", "analyzers": [], "findings": []},
                       ["no dead-code analyzers apply (no JS or Python roots)"])
    head = [run_analyzer(ctx, a, ctx.repo, "head") for a in analyzers]
    base_sha = base_desc = None
    base = []
    if not args.all:
        base_sha, base_desc = resolve_base(ctx, args.base)
        if ctx.dry_run:
            base = [dict(run_analyzer(ctx, a, "<base-worktree>", "base")) for a in analyzers]
        else:
            with base_worktree(ctx, base_sha) as wt:
                base = [run_analyzer(ctx, a, wt, "base") for a in analyzers]
    statuses = []
    summary = []
    findings = []
    resolved = []
    for h, b in zip(head, base or [None] * len(head)):
        ent = {"name": h["name"], "root": h["root"], "command": h.get("command"), "status": h["status"], "reason": h["reason"],
               "head_findings": len(h["findings"])}
        if h.get("output_tail"):
            ent["output_tail"] = h["output_tail"]
        if h.get("diagnostics"):
            ent["diagnostics"] = h["diagnostics"]
        if b is not None:
            ent["base_status"] = b["status"]
            ent["base_findings"] = len(b["findings"])
            if b["status"] not in ("ok", "dry-run") and ent["status"] in ("ok", "dry-run"):
                ent["status"] = b["status"]
                ent["reason"] = "base: %s" % b["reason"]
                if b.get("diagnostics"):
                    ent["base_diagnostics"] = b["diagnostics"]
                if b.get("output_tail"):
                    ent["base_output_tail"] = b["output_tail"]
        st = ent["status"]
        if st == "ok":
            if b is None:
                new = h["findings"]
            else:
                bkeys = {finding_key(f) for f in b["findings"]}
                hkeys = {finding_key(f) for f in h["findings"]}
                new = [f for f in h["findings"] if finding_key(f) not in bkeys]
                resolved += [f for f in b["findings"] if finding_key(f) not in hkeys]
            ent["new_findings" if b is not None else "findings"] = len(new)
            findings += new
            statuses.append("failed" if new else "passed")
        else:
            statuses.append("error" if st == "error" else ("dry-run" if st == "dry-run" else "unavailable"))
        summary.append(ent)
    exit_code = combine_exit(statuses)
    result = {"mode": "all" if args.all else "new", "base": base_sha, "base_ref": base_desc,
              "analyzers": summary, "findings": findings, "resolved": resolved}
    text = []
    if base_sha:
        text.append("base: %s (%s)" % (base_sha[:12], base_desc))
    if ctx.dry_run and not args.all:
        text.append("$ git worktree add --detach <tmp>/tree %s   # removed afterwards" % base_sha)
    for e in summary:
        cmd = shell_join(e["command"]) if e.get("command") else ""
        if ctx.dry_run:
            text.append("$ (cd %s && %s)" % (e["root"], cmd))
            continue
        if e["status"] == "ok":
            n = e.get("new_findings", e.get("findings", 0))
            text.append("%s @ %s: %d %s finding(s)" % (e["name"], e["root"], n, "new" if not args.all else ""))
        else:
            text.append("%s @ %s: %s: %s" % (e["name"], e["root"], e["status"].upper(), e["reason"]))
            for line in e.get("output_tail", []) + e.get("base_output_tail", []):
                text.append("    " + line)
    for f in findings:
        loc = "%s:%s" % (f["file"], f["line"]) if f.get("line") else f["file"]
        text.append("  [%s %s] %s %s" % (f["analyzer"], f["type"], loc, f["name"]))
    return Outcome(exit_code, result, text)
