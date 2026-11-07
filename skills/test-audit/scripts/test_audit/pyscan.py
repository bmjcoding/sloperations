"""Python test declarations via `ast`: test functions, Test* classes,
unittest.TestCase methods, @pytest.mark.parametrize row counts and ids."""

import ast
import fnmatch


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _value_id(node, argname, index):
    if isinstance(node, ast.Constant) and (node.value is None or isinstance(node.value, (str, int, float, bool))):
        return str(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return "-" + str(node.operand.value)
    return "%s%d" % (argname, index)


def _auto_id(elt, argnames, index):
    """pytest-style automatic id for one argvalues element (best effort)."""
    if isinstance(elt, ast.Call) and _dotted(elt.func).endswith("param"):
        vals = elt.args
    elif len(argnames) > 1 and isinstance(elt, (ast.Tuple, ast.List)):
        vals = elt.elts
    else:
        vals = [elt]
    names = argnames or ["arg"]
    return "-".join(_value_id(v, names[k] if k < len(names) else "arg", index) for k, v in enumerate(vals))


def _param(dec):
    """(rows, ids) for a parametrize decorator, or None if not one."""
    if not isinstance(dec, ast.Call):
        return None
    name = _dotted(dec.func)
    if not (name.endswith("mark.parametrize") or name == "parametrize"):
        return None
    values = dec.args[1] if len(dec.args) > 1 else None
    ids_node = dec.args[3] if len(dec.args) > 3 else None
    for kw in dec.keywords:
        if kw.arg == "argvalues":
            values = kw.value
        elif kw.arg == "ids":
            ids_node = kw.value
    if not isinstance(values, (ast.List, ast.Tuple)):
        return None, None
    rows = len(values.elts)
    names_node = dec.args[0] if dec.args else next((k.value for k in dec.keywords if k.arg == "argnames"), None)
    argnames = []
    if _const_str(names_node) is not None:
        argnames = [a.strip() for a in _const_str(names_node).split(",") if a.strip()]
    elif isinstance(names_node, (ast.List, ast.Tuple)):
        argnames = [_const_str(e) or "arg" for e in names_node.elts]
    ids = [_auto_id(e, argnames, i) for i, e in enumerate(values.elts)]
    if isinstance(ids_node, (ast.List, ast.Tuple)):
        for i, e in enumerate(ids_node.elts[:rows]):
            if _const_str(e) is not None:
                ids[i] = _const_str(e)
    for i, e in enumerate(values.elts):
        if isinstance(e, ast.Call) and _dotted(e.func).endswith("param"):
            for kw in e.keywords:
                if kw.arg == "id" and _const_str(kw.value) is not None:
                    ids[i] = _const_str(kw.value)
    return rows, ids


def _params(decorators):
    """Combine stacked parametrize decorators: rows multiply, ids join with '-'."""
    found = [p for p in (_param(d) for d in decorators) if p is not None]
    if not found:
        return False, None, None
    if any(r is None for r, _ in found):
        return True, None, None
    rows, ids = 1, [""]
    # pytest applies the bottom decorator first; ids read top-to-bottom reversed
    for r, i in reversed(found):
        rows *= r
        ids = [(a + "-" if a else "") + b for a in ids for b in i]
    return True, rows, ids


def _marks(decorators):
    out = []
    for d in decorators:
        name = _dotted(d.func if isinstance(d, ast.Call) else d)
        for m in ("skip", "skipif", "xfail", "asyncio", "slow"):
            if name.endswith("mark." + m):
                out.append(m)
        if name.endswith("unittest.skip") or name in ("skip", "skipIf", "skipUnless", "expectedFailure") or name.startswith("unittest.skip") or name == "unittest.expectedFailure":
            out.append(name.rsplit(".", 1)[-1])
    return out


def _is_testcase(cls):
    for b in cls.bases:
        n = _dotted(b)
        if n.endswith("TestCase") or n.endswith("IsolatedAsyncioTestCase"):
            return True
    return False


def scan(src, filename="<test>", python_functions=("test*",), python_classes=("Test*",)):
    tree = ast.parse(src, filename=filename)
    out = []

    def fn_ok(name):
        return any(fnmatch.fnmatchcase(name, p) for p in python_functions)

    def cls_ok(node):
        if _is_testcase(node):
            return True
        if not any(fnmatch.fnmatchcase(node.name, p) for p in python_classes):
            return False
        return not any(isinstance(b, ast.FunctionDef) and b.name == "__init__" for b in node.body)

    def visit(body, path, inherited_params, in_testcase, cls_marks):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not (fn_ok(node.name) or (in_testcase and node.name.startswith("test"))):
                    continue
                is_p, rows, ids = _params(node.decorator_list)
                if inherited_params is not None and inherited_params[0]:
                    _, c_rows, c_ids = inherited_params
                    if c_rows is None or (is_p and rows is None):
                        rows, ids = None, None
                    else:
                        base_rows, base_ids = (rows, ids) if is_p else (1, [""])
                        rows = base_rows * c_rows
                        ids = [(a + "-" if a else "") + b for a in base_ids for b in c_ids]
                    is_p = True
                out.append({
                    "line": node.lineno, "col": node.col_offset + 1, "path": path + [node.name],
                    "kind": "each" if is_p else "test", "rows": rows if is_p else None,
                    "row_ids": ids if is_p else None,
                    "modifiers": cls_marks + _marks(node.decorator_list),
                })
            elif isinstance(node, ast.ClassDef) and cls_ok(node):
                cp = _params(node.decorator_list)
                visit(node.body, path + [node.name], cp if cp[0] else inherited_params,
                      in_testcase or _is_testcase(node), cls_marks + _marks(node.decorator_list))

    visit(tree.body, [], None, False, [])
    return out
