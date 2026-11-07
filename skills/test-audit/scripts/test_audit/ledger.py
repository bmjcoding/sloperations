"""ledger: every test declaration with blank mark/evidence columns."""

import csv
import io
import os

from . import classify, jsscan, pyscan
from .core import EXIT_OK, Outcome


def _py_patterns(ctx, path):
    from . import pytestcfg

    cfg = pytestcfg.for_file(ctx, path)
    return tuple(cfg["python_functions"]), tuple(cfg["python_classes"])


def scan_file(ctx, rel):
    full = ctx.abspath(rel)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
    except OSError as e:
        return None, str(e)
    if rel.endswith(".py"):
        funcs, classes = _py_patterns(ctx, rel)
        try:
            return pyscan.scan(src, rel, funcs, classes), None
        except SyntaxError as e:
            return None, "syntax error: %s" % e
    return [d for d in jsscan.scan(src) if d["kind"] != "suite"], None


def rows_for(ctx, files, expand):
    rows, errors = [], []
    for rel in files:
        decls, err = scan_file(ctx, rel)
        if err:
            errors.append({"file": rel, "error": err})
            continue
        for d in sorted(decls, key=lambda x: (x["line"], x["col"])):
            sep = "::" if rel.endswith(".py") else " > "
            base = {
                "file": rel, "line": d["line"], "name": sep.join(d["path"]), "path": d["path"],
                "kind": d["kind"], "rows": d["rows"],
                "modifiers": sorted(set(d.get("modifiers", []) + d.get("inherited_modifiers", []))),
                "mark": "", "evidence": "",
            }
            if d.get("suite_rows"):
                base["suite_rows"] = d["suite_rows"]
            if expand and d["kind"] == "each" and d["rows"]:
                ids = d.get("row_ids") or [None] * d["rows"]
                for i in range(d["rows"]):
                    rid = ids[i] if i < len(ids) and ids[i] is not None else str(i)
                    r = dict(base, kind="row", row_index=i, row_id=rid, name="%s[%s]" % (base["name"], rid))
                    rows.append(r)
            else:
                if d.get("row_ids"):
                    base["row_ids"] = d["row_ids"]
                rows.append(base)
    return rows, errors


def _md(rows):
    def esc(v):
        return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")

    out = ["| file | line | name | kind | rows | modifiers | mark | evidence |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append("| %s |" % " | ".join(esc(x) for x in (r["file"], r["line"], r["name"], r["kind"], r["rows"] if r["rows"] is not None else ("?" if r["kind"] == "each" else ""), ",".join(r["modifiers"]), r["mark"], r["evidence"])))
    return out


def _csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["file", "line", "name", "kind", "rows", "modifiers", "mark", "evidence"])
    for r in rows:
        w.writerow([r["file"], r["line"], r["name"], r["kind"], "" if r["rows"] is None else r["rows"], ",".join(r["modifiers"]), r["mark"], r["evidence"]])
    return buf.getvalue().rstrip("\n").split("\n")


def cmd_ledger(ctx, args):
    scopes = []
    for p in args.paths or []:
        scopes.append(ctx.resolve_user_path(p))
    files = []
    for s in scopes or [None]:
        if s is not None and os.path.isfile(ctx.abspath(s)):
            files.append(s)  # explicit files are scanned even if not classified as tests
        else:
            files += classify.test_files(ctx, [s] if s else None)
    files = sorted(set(files))
    rows, errors = rows_for(ctx, files, args.expand_rows)
    if args.format == "json":
        ctx.json = True
    result = {"files": files, "declarations": rows, "errors": errors,
              "counts": {"files": len(files), "declarations": len(rows)}}
    text = _md(rows) if args.format == "md" else _csv(rows)
    for e in errors:
        ctx.warn("%s: %s" % (e["file"], e["error"]))
    return Outcome(EXIT_OK, result, text)
