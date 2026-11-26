"""Alembic helper for slop-check (Python 3.9+).

Reads the [schema] table of cleanup.toml as JSON on stdin and writes one JSON document.

Default mode (run isolated, standard library only, no application imports): the revision chain.
Revision files are read with ast: duplicate ids, a down_revision or depends_on naming a missing
revision, cycles, and heads that differ from expected_heads (a count, or a list of head ids and
branch labels, where a revision inherits the labels of its ancestors). Writes {heads, findings}.

--models mode (run by the repository's test environment, only when the reviewer asks for it):
imports target_metadata and model_packages, flags model modules the metadata does not import,
runs configure_mappers(), and writes {tables, mappers, associations, findings}.
"""
import ast
import itertools
import json
import os
import sys


def finding(rule, path, message, fp):
    return {"rule": "schema/" + rule, "path": path, "line": 0, "severity": "error", "message": message, "fp": fp}


def as_list(value):
    return [] if value is None else [value] if isinstance(value, str) else list(value)


def read_revisions(versions, findings):
    if not versions or not os.path.isdir(versions):
        raise SystemExit("alembic helper: [schema] migrations must name the Alembic versions directory, got %r" % versions)
    revs = {}
    for name in sorted(os.listdir(versions)):
        if not name.endswith(".py"):
            continue
        rel = "%s/%s" % (versions.rstrip("/"), name)
        with open(rel, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), rel)
        vals = {}
        for stmt in tree.body:
            target = stmt.targets[0] if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 else getattr(stmt, "target", None)
            if isinstance(target, ast.Name) and getattr(stmt, "value", None) is not None and target.id in ("revision", "down_revision", "branch_labels", "depends_on"):
                vals[target.id] = ast.literal_eval(stmt.value)
        if "revision" not in vals:
            continue
        rid = vals["revision"]
        if rid in revs:
            findings.append(finding("duplicate-revision", rel, "revision %s is also declared in %s" % (rid, revs[rid]["path"]), rid))
        revs[rid] = {"path": rel, "down": as_list(vals.get("down_revision")), "deps": as_list(vals.get("depends_on")), "labels": as_list(vals.get("branch_labels"))}
    return revs


def revision_graph(versions, expected, findings):
    revs = read_revisions(versions, findings)
    for rid, rev in sorted(revs.items()):
        for ref in rev["down"] + rev["deps"]:
            if ref not in revs and not any(ref in r["labels"] for r in revs.values()):
                findings.append(finding("broken-chain", rev["path"], "revision %s names missing revision %s" % (rid, ref), "%s->%s" % (rid, ref)))
    state, labels = {}, {}

    def ancestry(rid, trail):  # inherited labels, with cycle detection
        if state.get(rid) == "done":
            return labels[rid]
        if state.get(rid) == "open":
            findings.append(finding("cycle", revs[rid]["path"], "revision %s is its own ancestor (%s)" % (rid, " -> ".join(trail + [rid])), "cycle:%s" % rid))
            return set()
        state[rid] = "open"
        found = set(revs[rid]["labels"])
        for down in revs[rid]["down"]:
            if down in revs:
                found |= ancestry(down, trail + [rid])
        state[rid], labels[rid] = "done", found
        return found

    for rid in sorted(revs):
        ancestry(rid, [])
    referenced = {d for r in revs.values() for d in r["down"]}
    heads = sorted(r for r in revs if r not in referenced)
    if isinstance(expected, list):
        names = [set([h]) | labels.get(h, set()) for h in heads]
        fits = len(heads) == len(expected) and any(all(e in n for e, n in zip(p, names)) for p in itertools.permutations(expected))
        if not fits:
            findings.append(finding("heads", versions, "heads are %s (labels %s); cleanup.toml expects %s" % (heads, [sorted(labels.get(h, ())) for h in heads], sorted(expected)), "heads"))
    elif len(heads) != int(expected):
        findings.append(finding("heads", versions, "%d head(s) %s; cleanup.toml expects %d" % (len(heads), heads, int(expected)), "heads"))
    return heads


def models(cfg, findings):
    import importlib
    import pkgutil

    sys.path.insert(0, os.getcwd())
    where = cfg["target_metadata"]
    try:
        from sqlalchemy import inspect
        from sqlalchemy.orm import configure_mappers
    except ModuleNotFoundError:
        sys.stderr.write("target_metadata is set but this Python cannot import sqlalchemy\n")
        sys.exit(3)
    try:
        module, _, attr = where.partition(":")
        metadata = importlib.import_module(module)
        for part in filter(None, attr.split(".")):
            metadata = getattr(metadata, part)
        loaded = set(metadata.tables)
        for pkg_name in cfg.get("model_packages", []):
            pkg = importlib.import_module(pkg_name)
            for info in sorted(pkgutil.walk_packages(pkg.__path__, pkg_name + "."), key=lambda i: i.name):
                importlib.import_module(info.name)
        for table in sorted(set(metadata.tables) - loaded):
            findings.append(finding("unimported-model", where, "table %s is defined in a model module that %s does not import" % (table, where), table))
        configure_mappers()
    except Exception as exc:  # the model failure is the finding; the revision graph still reports
        rule = "row-identity" if "primary key" in str(exc).lower() else "model-import"
        findings.append(finding(rule, where, "%s: %s" % (type(exc).__name__, exc), rule))
        return {}
    default = (cfg.get("schemas") or ["public"])[0]
    qualify = lambda t: "%s.%s" % (t.schema or default, t.name)
    out = {"tables": {qualify(t): {"columns": {c.name: {"nullable": bool(c.nullable)} for c in t.columns}} for t in metadata.sorted_tables}}
    import gc

    mapped, mappers = set(), []
    for obj in gc.get_objects():  # every mapped class over this metadata (public API only)
        mapper = obj.__dict__.get("__mapper__") if isinstance(obj, type) else None
        if mapper is not None and getattr(mapper.local_table, "metadata", None) is metadata and obj is mapper.class_:
            mapped.add(qualify(mapper.local_table))
            mappers.append({"table": qualify(mapper.local_table), "class": obj.__name__, "key": sorted(c.name for c in inspect(obj).primary_key)})
    out["mappers"] = sorted(mappers, key=lambda m: (m["table"], m["class"]))
    out["associations"] = sorted(qualify(t) for t in metadata.sorted_tables if qualify(t) not in mapped
                                 and len(t.foreign_keys) >= 2 and all(c.foreign_keys or c.primary_key for c in t.columns))
    return out


def main():
    cfg = json.load(sys.stdin)
    findings = []
    if "--models" in sys.argv:
        out = models(cfg, findings)
    else:
        out = {"heads": revision_graph(cfg.get("migrations", ""), cfg.get("expected_heads", 1), findings)}
    out["findings"] = findings
    json.dump(out, sys.stdout, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
