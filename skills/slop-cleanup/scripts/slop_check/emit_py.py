"""Python emitter for slop-check (standard library only, Python 3.9+; run isolated with -I -S -B).

Reads {root, files, loggers, levels, event_arg, fields_arg} as JSON on stdin and writes one JSON
record per line: comments, docstrings, function ranges, log calls and SQL candidates. Names resolve
through the scope that binds them (function, then module, then a module-level constant imported
from another scanned file). A name bound any other way, a parameter, or any expression this file
cannot evaluate statically is unresolved: SQL built from it is emitted with resolved=false.
"""
import ast
import io
import json
import sys
import tokenize

SQL_WORD = ("select ", "insert ", "update ", "delete ", " from ", " join ", " into ", "returning ", " where ")
BUILDERS = {"SQL", "Identifier", "Literal", "Placeholder", "Composed", "format", "join"}
EXECUTE = {"execute", "executemany", "stream", "copy"}
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def name_of(node):  # info for log.info, Identifier for sql.Identifier
    return node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)


def is_str(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def looks_sql(text):
    return any(w in " %s " % text.lower() for w in SQL_WORD)


class Scope:
    """Names one scope binds: static values (str constants, SQL compositions, dicts), imports, and the rest."""

    def __init__(self, node, parent):
        self.parent, self.values, self.other, self.imports = parent, {}, set(), {}
        args = node.args if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) else None
        if args:
            for a in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
                if a:
                    self.other.add(a.arg)
        body = node.body if isinstance(node.body, list) else [node.body]
        todo = list(body)
        while todo:
            n = todo.pop()
            if isinstance(n, SCOPES):
                if not isinstance(n, ast.Lambda):
                    self.other.add(n.name)
                continue
            target = n.targets[0] if isinstance(n, ast.Assign) and len(n.targets) == 1 else getattr(n, "target", None)
            if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(target, ast.Name) and n.value is not None:
                self.values.setdefault(target.id, []).append(n.value)
                todo.append(n.value)
                continue
            if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                for a in n.names:
                    self.imports[a.asname or a.name] = (n.module, a.name)
                continue
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                self.other.update((a.asname or a.name).split(".")[0] for a in n.names)
            if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load) or isinstance(n, (ast.Global, ast.Nonlocal)):
                self.other.update(getattr(n, "names", [getattr(n, "id", "")]))
            todo.extend(ast.iter_child_nodes(n))

    def lookup(self, name):
        """The value nodes bound to name, or None when it is unknown or bound dynamically."""
        scope = self
        while scope:
            if name in scope.other:
                return None
            if name in scope.values:
                return scope.values[name]
            if name in scope.imports:
                module, attr = scope.imports[name]
                mod = MODULES.get(module)
                values = mod.values.get(attr) if mod and attr not in mod.other else None
                return values if values and all(is_str(v) for v in values) else None
            scope = scope.parent
        return None


MODULES = {}  # dotted module name -> module Scope, for constants imported between scanned files


def sql_parts(node, scope, depth=0):
    """Literal SQL text and identifiers under node, and whether all of it is known statically."""
    text, idents, ok = [], [], True
    if depth > 8:
        return "", [], False
    if isinstance(node, ast.Call) and name_of(node.func) in ("Literal", "Placeholder"):
        return "", [], True
    if is_str(node):
        return node.value, [], True
    if isinstance(node, ast.Name):
        values = scope.lookup(node.id)
        if not values:
            return "", [], False
        for v in values:
            t, i, k = sql_parts(v, scope, depth + 1)
            text.append(t), idents.extend(i)
            ok = ok and k
        return " ".join(text), idents, ok
    if isinstance(node, ast.Call):
        callee = node.func
        if name_of(callee) not in BUILDERS:
            return "", [], False
        if name_of(callee) == "Identifier":
            idents.extend(a.value for a in node.args if is_str(a))
        parts = list(node.args) + [k.value for k in node.keywords]
        if name_of(callee) in ("format", "join") and isinstance(callee, ast.Attribute):
            parts.append(callee.value)  # the receiver: a SQL() call, a string, or a name bound to one
    elif isinstance(node, (ast.JoinedStr, ast.BinOp, ast.Tuple, ast.List, ast.FormattedValue)):
        ok = not isinstance(node, (ast.JoinedStr, ast.FormattedValue))
        parts = list(ast.iter_child_nodes(node))
    else:
        return "", [], False
    for p in parts:
        if isinstance(p, (ast.operator, ast.expr_context)):
            continue
        t, i, k = sql_parts(p, scope, depth + 1)
        text.append(t), idents.extend(i)
        ok = ok and k
    return " ".join(t for t in text if t), idents, ok


def logger_root(node, scope, loggers, depth=0):
    """True when a call target such as log.bind(x).info or self.log.info roots at a configured logger."""
    while depth < 8:
        depth += 1
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            if node.attr in loggers:
                return True
            node = node.value
        elif isinstance(node, ast.Name):
            if node.id in loggers:
                return True
            values = scope.lookup(node.id)
            return bool(values) and len(values) == 1 and isinstance(values[0], (ast.Call, ast.Attribute)) and logger_root(values[0], scope, loggers, depth)
        else:
            return False
    return False


def event_of(node, scope):
    if is_str(node):
        return {"kind": "literal", "values": [node.value]}
    if isinstance(node, ast.IfExp):
        a, b = event_of(node.body, scope), event_of(node.orelse, scope)
        if a["kind"] == b["kind"] == "literal":
            return {"kind": "literal", "values": a["values"] + b["values"]}
        return {"kind": "dynamic"}
    values = scope.lookup(node.id) if isinstance(node, ast.Name) else None
    if values and all(is_str(v) for v in values):
        return {"kind": "literal", "values": [v.value for v in values]}
    return {"kind": "dynamic", "variable": isinstance(node, ast.Name)}


def dict_fields(node, scope, out):
    """Add a dict's literal keys to out; True when a spread, computed key or unknown value hides more."""
    if isinstance(node, ast.Name):
        values = scope.lookup(node.id)
        node = values[0] if values and len(values) == 1 else node
    if not isinstance(node, ast.Dict):
        return True
    out.extend(str(k.value) for k in node.keys if isinstance(k, ast.Constant))
    return any(not isinstance(k, ast.Constant) for k in node.keys)


def emit_file(rel, src, tree, cfg, out):
    try:
        if not isinstance(tree, ast.AST):
            raise tree
        comments = [t for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type == tokenize.COMMENT]
    except (SyntaxError, ValueError, tokenize.TokenError) as exc:
        out({"kind": "error", "path": rel, "message": "cannot parse: %s" % exc})
        return
    for tok in comments:
        own = not tok.line[: tok.start[1]].strip()
        out({"kind": "comment", "path": rel, "line": tok.start[0], "text": tok.string[1:], "own_line": own})
    loggers, levels = set(cfg["loggers"]), set(cfg["levels"])
    ev_i, fd_i = cfg["event_arg"], cfg["fields_arg"]
    docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr) and is_str(n.body[0].value)}
    covered = set()  # nodes inside a composition already emitted as one SQL site

    def visit(node, scope, symbol):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and id(getattr(body[0], "value", None)) in docs:
            out({"kind": "docstring", "path": rel, "line": body[0].lineno, "text": body[0].value.value})
        for child in ast.iter_child_nodes(node):
            if isinstance(child, SCOPES):
                name = "%s.%s" % (symbol, child.name) if symbol and hasattr(child, "name") else getattr(child, "name", symbol)
                if hasattr(child, "name"):
                    out({"kind": "symbol", "path": rel, "line": child.lineno, "end": child.end_lineno, "name": name})
                visit(child, Scope(child, scope), name)
                continue
            composed = isinstance(child, (ast.JoinedStr, ast.BinOp)) or isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr in ("format", "join")
            if composed and id(child) not in covered:  # f-strings, + and %, .format() and .join(): one site, resolved only if static
                text, idents, ok = sql_parts(child, scope)
                if looks_sql(text) or idents:
                    covered.update(id(n) for n in ast.walk(child))
                    out({"kind": "sql", "path": rel, "line": child.lineno, "text": text, "idents": idents, "resolved": ok})
            elif is_str(child) and id(child) not in docs and id(child) not in covered and looks_sql(child.value):
                out({"kind": "sql", "path": rel, "line": child.lineno, "text": child.value, "idents": [], "resolved": True})
            if isinstance(child, ast.Call):
                call(child, scope)
            visit(child, scope, symbol)

    def call(node, scope):
        func, args = node.func, node.args
        if id(node) in covered:
            return
        ev = args[ev_i] if len(args) > ev_i else next((k.value for k in node.keywords if k.arg in ("msg", "event")), None)
        if isinstance(func, ast.Attribute) and func.attr in levels and logger_root(func.value, scope, loggers) and (ev is not None or node.keywords):
            fields, spread = [], False
            for kw in node.keywords:
                if kw.arg in ("msg", "event") and len(args) <= ev_i:
                    continue
                if kw.arg is None or kw.arg == "extra":
                    spread = (dict_fields(kw.value, scope, fields) if kw.arg else True) or spread
                elif kw.arg not in ("exc_info", "stack_info", "stacklevel"):
                    fields.append(kw.arg)  # structlog-style keyword fields
            if fd_i != ev_i and len(args) > fd_i and isinstance(args[fd_i], (ast.Dict, ast.Name)):
                spread = dict_fields(args[fd_i], scope, fields) or spread  # other positionals are format arguments
            event = event_of(ev, scope) if ev is not None else {"kind": "dynamic"}
            out({"kind": "log", "path": rel, "line": node.lineno, "event": event, "fields": fields, "spread": spread})
            return
        query = args[0] if args else next((k.value for k in node.keywords if k.arg == "query"), None)
        if isinstance(func, ast.Attribute) and func.attr in EXECUTE and query is not None:
            target = query
        elif name_of(func) in ("SQL", "Identifier"):
            target = node
        else:
            return
        covered.update(id(n) for n in ast.walk(node))
        text, idents, ok = sql_parts(target, scope)
        out({"kind": "sql", "path": rel, "line": node.lineno, "text": text, "idents": idents, "resolved": ok})

    visit(tree, MODULES[module_name(rel)], "")


def module_name(rel):
    name = rel[:-3].replace("/", ".")
    return name[: -len(".__init__")] if name.endswith(".__init__") else name


def main():
    cfg = json.load(sys.stdin)
    parsed = {}
    for rel in cfg["files"]:
        with open("%s/%s" % (cfg["root"], rel), encoding="utf-8") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError) as exc:
            tree = exc  # reported by emit_file
        parsed[rel] = (src, tree)
        MODULES[module_name(rel)] = Scope(tree if isinstance(tree, ast.AST) else ast.Module(body=[], type_ignores=[]), None)
    for rel, (src, tree) in parsed.items():
        emit_file(rel, src, tree, cfg, lambda rec: print(json.dumps(rec, sort_keys=True)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
