"""Light JS/TS scanner for test declarations. Skips strings, comments,
template literals (with nested ${}), and regex literals; then finds
describe/it/test call chains. Heuristic by design: no type information."""

import re

SCOPES = {"describe": None, "suite": None, "context": None, "fdescribe": "only", "xdescribe": "skip", "xcontext": "skip"}
TESTS = {"it": None, "test": None, "fit": "only", "xit": "skip", "xtest": "skip", "specify": None}
MODIFIERS = {"skip", "only", "todo", "concurrent", "sequential", "fails", "failing", "each", "for", "skipIf", "runIf", "shuffle", "serial", "if"}
_ID = re.compile(r"[A-Za-z_$][\w$]*")
_NUM = re.compile(r"(?:0[xXoObB][0-9a-fA-F_]+|\d[\d_]*(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)n?")
_REGEX_PREV_KW = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case", "do", "else", "yield", "await"}
_PUNCT3 = ("===", "!==", "...", "**=", "<<=", ">>=", ">>>", "&&=", "||=", "??=")
_PUNCT2 = ("=>", "==", "!=", "<=", ">=", "&&", "||", "??", "?.", "++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "**", "<<", ">>")


class Tok:
    __slots__ = ("kind", "value", "start", "end", "line", "col", "has_expr")

    def __init__(self, kind, value, start, end, line, col, has_expr=False):
        self.kind, self.value, self.start, self.end = kind, value, start, end
        self.line, self.col, self.has_expr = line, col, has_expr


class _Lexer:
    def __init__(self, src):
        self.s = src
        self.n = len(src)
        self.toks = []
        self._line_starts = [0]
        for m in re.finditer("\n", src):
            self._line_starts.append(m.end())

    def pos(self, i):
        lo, hi = 0, len(self._line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._line_starts[mid] <= i:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, i - self._line_starts[lo] + 1

    def regex_allowed(self):
        if not self.toks:
            return True
        t = self.toks[-1]
        if t.kind == "punct":
            return t.value not in (")", "]", "}", "++", "--")
        if t.kind == "id":
            return t.value in _REGEX_PREV_KW
        return False

    def skip_string(self, i, q):
        j = i + 1
        out = []
        while j < self.n:
            c = self.s[j]
            if c == "\\":
                nxt = self.s[j + 1 : j + 2]
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                j += 2
                continue
            if c == q:
                return j + 1, "".join(out)
            if c == "\n":  # unterminated (e.g. JSX text): stop at end of line
                return j, "".join(out)
            out.append(c)
            j += 1
        return j, "".join(out)

    def skip_template(self, i):
        """i at the opening backtick; returns (end, raw, has_expr)."""
        j = i + 1
        has_expr = False
        while j < self.n:
            c = self.s[j]
            if c == "\\":
                j += 2
                continue
            if c == "`":
                return j + 1, self.s[i + 1 : j], has_expr
            if c == "$" and self.s[j + 1 : j + 2] == "{":
                has_expr = True
                j = self.skip_code(j + 2, "}")
                continue
            j += 1
        return j, self.s[i + 1 : j], has_expr

    def skip_code(self, i, closer):
        """Skip code until the matching closer at depth 0; returns index after it."""
        depth = 0
        j = i
        prev_sig = "("
        while j < self.n:
            c = self.s[j]
            if c in " \t\r\n":
                j += 1
                continue
            if c == "/" and self.s[j + 1 : j + 2] == "/":
                k = self.s.find("\n", j)
                j = self.n if k == -1 else k
                continue
            if c == "/" and self.s[j + 1 : j + 2] == "*":
                k = self.s.find("*/", j + 2)
                j = self.n if k == -1 else k + 2
                continue
            if c in "'\"":
                j, _ = self.skip_string(j, c)
                prev_sig = "a"
                continue
            if c == "`":
                j, _, _ = self.skip_template(j)
                prev_sig = "a"
                continue
            if c == "/" and prev_sig in "(,=:[!&|?{};+-*%<>~^":
                j = self.skip_regex(j)
                prev_sig = "a"
                continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0 and c == closer:
                    return j + 1
                depth -= 1
            prev_sig = c if not (c.isalnum() or c in "_$") else "a"
            j += 1
        return j

    def skip_regex(self, i):
        j = i + 1
        in_cls = False
        while j < self.n:
            c = self.s[j]
            if c == "\\":
                j += 2
                continue
            if c == "\n":
                return j
            if in_cls:
                if c == "]":
                    in_cls = False
            elif c == "[":
                in_cls = True
            elif c == "/":
                j += 1
                while j < self.n and (self.s[j].isalnum() or self.s[j] == "_"):
                    j += 1
                return j
            j += 1
        return j

    def run(self):
        s, n = self.s, self.n
        i = 0
        while i < n:
            c = s[i]
            if c in " \t\r\n":
                i += 1
                continue
            if s.startswith("//", i) or (i == 0 and s.startswith("#!")):
                k = s.find("\n", i)
                i = n if k == -1 else k
                continue
            if s.startswith("/*", i):
                k = s.find("*/", i + 2)
                i = n if k == -1 else k + 2
                continue
            line, col = self.pos(i)
            if c in "'\"":
                j, val = self.skip_string(i, c)
                self.toks.append(Tok("str", val, i, j, line, col))
                i = j
                continue
            if c == "`":
                j, raw, has_expr = self.skip_template(i)
                self.toks.append(Tok("tpl", raw, i, j, line, col, has_expr))
                i = j
                continue
            if c == "/" and self.regex_allowed():
                j = self.skip_regex(i)
                self.toks.append(Tok("regex", s[i:j], i, j, line, col))
                i = j
                continue
            m = _ID.match(s, i)
            if m:
                self.toks.append(Tok("id", m.group(0), i, m.end(), line, col))
                i = m.end()
                continue
            m = _NUM.match(s, i)
            if m and m.end() > i:
                self.toks.append(Tok("num", m.group(0), i, m.end(), line, col))
                i = m.end()
                continue
            for p in _PUNCT3 + _PUNCT2:
                if s.startswith(p, i):
                    self.toks.append(Tok("punct", p, i, i + len(p), line, col))
                    i += len(p)
                    break
            else:
                self.toks.append(Tok("punct", c, i, i + 1, line, col))
                i += 1
        return self.toks


def tokenize(src):
    return _Lexer(src).run()


def _match(toks, i):
    """Index of the bracket matching toks[i] ('(' or '[')."""
    depth = 0
    for j in range(i, len(toks)):
        t = toks[j]
        if t.kind != "punct":
            continue
        if t.value in ("(", "[", "{"):
            depth += 1
        elif t.value in (")", "]", "}"):
            depth -= 1
            if depth == 0:
                return j
    return len(toks) - 1


def _array_rows(toks, i, end):
    """Count top-level elements of an array literal toks[i]=='[' .. toks[end]==']'."""
    if end == i + 1:
        return 0
    count, depth, last_comma = 1, 0, False
    for j in range(i + 1, end):
        t = toks[j]
        last_comma = False
        if t.kind == "punct":
            if t.value in ("(", "[", "{"):
                depth += 1
            elif t.value in (")", "]", "}"):
                depth -= 1
            elif t.value == "," and depth == 0:
                count += 1
                last_comma = True
    if last_comma:
        count -= 1
    return count


def _table_rows(raw):
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return 0
    return max(len(lines) - 1, 0)


def _name_of(src, toks, a, b):
    """Name from argument tokens toks[a:b]."""
    if b - a == 1 and toks[a].kind == "str":
        return toks[a].value
    if b - a == 1 and toks[a].kind == "tpl":
        return toks[a].value
    if a >= b:
        return "<anonymous>"
    return src[toks[a].start : toks[b - 1].end]


def scan(src):
    """Return declarations: dicts with line, col, path, kind, rows, modifiers."""
    toks = tokenize(src)
    out = []
    stack = []  # (name, end_index, rows, is_each, modifiers)
    i = 0
    n = len(toks)
    while i < n:
        while stack and i > stack[-1][1]:
            stack.pop()
        t = toks[i]
        if t.kind != "id" or (t.value not in SCOPES and t.value not in TESTS):
            i += 1
            continue
        prev = toks[i - 1] if i else None
        if prev is not None and ((prev.kind == "punct" and prev.value in (".", "?.")) or (prev.kind == "id" and prev.value in ("function", "class", "const", "let", "var", "import", "export", "as"))):
            i += 1
            continue
        base = t.value
        mods = []
        implied = SCOPES.get(base) if base in SCOPES else TESTS.get(base)
        if implied:
            mods.append(implied)
        rows = None
        is_each = False
        j = i + 1
        ok = True
        while j < n:
            tj = toks[j]
            if tj.kind == "punct" and tj.value == "." and j + 1 < n and toks[j + 1].kind == "id":
                m = toks[j + 1].value
                if m not in MODIFIERS:
                    ok = False
                    break
                j += 2
                if m in ("each", "for"):
                    is_each = True
                    mods.append(m)
                    if j < n and toks[j].kind == "tpl":
                        rows = _table_rows(toks[j].value)
                        j += 1
                    elif j < n and toks[j].kind == "punct" and toks[j].value == "(":
                        close = _match(toks, j)
                        if j + 1 < n and toks[j + 1].kind == "punct" and toks[j + 1].value == "[":
                            arr_end = _match(toks, j + 1)
                            if arr_end + 1 == close or (arr_end + 2 == close and toks[arr_end + 1].value == ","):
                                rows = _array_rows(toks, j + 1, arr_end)
                        j = close + 1
                    continue
                mods.append(m)
                if m in ("skipIf", "runIf", "if") and j < n and toks[j].value == "(":
                    j = _match(toks, j) + 1
                continue
            break
        if not ok or j >= n or toks[j].kind != "punct" or toks[j].value != "(":
            i += 1
            continue
        close = _match(toks, j)
        # first argument
        k, depth = j + 1, 0
        while k < close:
            tk = toks[k]
            if tk.kind == "punct":
                if tk.value in ("(", "[", "{"):
                    depth += 1
                elif tk.value in (")", "]", "}"):
                    depth -= 1
                elif tk.value == "," and depth == 0:
                    break
            k += 1
        if k == j + 1:  # no arguments: not a declaration (e.g. a local `test()`)
            i += 1
            continue
        name = _name_of(src, toks, j + 1, k)
        path = [s[0] for s in stack] + [name]
        if base in SCOPES:
            out.append({"line": t.line, "col": t.col, "path": path, "kind": "suite", "rows": rows if is_each else None, "modifiers": mods})
            stack.append((name, close, rows, is_each, mods))
            i = j + 1
            continue
        inherited = sorted({m for s in stack for m in s[4] if m in ("skip", "only", "todo", "concurrent")} - set(mods))
        out.append({"line": t.line, "col": t.col, "path": path, "kind": "each" if is_each else "test", "rows": rows if is_each else None,
                    "modifiers": mods, "inherited_modifiers": inherited,
                    "suite_rows": [s[2] for s in stack if s[3]]})
        i = j + 1
    return out
