"""TOML loading: tomllib on 3.11+, a small compatible subset parser on 3.9/3.10.

The fallback covers what pyproject.toml files use in practice: tables, array
tables, dotted/quoted keys, strings (basic, literal, multi-line), integers,
floats, booleans, arrays, inline tables. Dates are returned as strings.
"""

import re

try:  # pragma: no cover - depends on interpreter
    import tomllib as _tomllib
except ImportError:  # pragma: no cover
    _tomllib = None


class TomlError(ValueError):
    pass


def loads(text):
    if _tomllib is not None:
        try:
            return _tomllib.loads(text)
        except _tomllib.TOMLDecodeError as e:
            raise TomlError(str(e)) from None
    return _Parser(text).parse()


def load_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return loads(f.read())


_BARE = re.compile(r"[A-Za-z0-9_-]+")
_ATOM = re.compile(r"[0-9A-Za-z_+\-.:]+(?:[ T][0-9:.+\-Z]+)?")
_ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\", "e": "\x1b"}


class _Parser:
    def __init__(self, text):
        self.s = text.replace("\r\n", "\n")
        self.i = 0
        self.root = {}

    def err(self, msg):
        line = self.s.count("\n", 0, self.i) + 1
        raise TomlError("%s (line %d)" % (msg, line))

    def peek(self, k=0):
        j = self.i + k
        return self.s[j] if j < len(self.s) else ""

    def ws(self):
        while self.peek() in (" ", "\t"):
            self.i += 1

    def ws_nl(self):
        while True:
            c = self.peek()
            if c in (" ", "\t", "\n"):
                self.i += 1
            elif c == "#":
                while self.peek() not in ("\n", ""):
                    self.i += 1
            else:
                return

    def eol(self):
        self.ws()
        if self.peek() == "#":
            while self.peek() not in ("\n", ""):
                self.i += 1
        if self.peek() not in ("\n", ""):
            self.err("expected end of line")

    def parse(self):
        current = self.root
        while True:
            self.ws_nl()
            if self.i >= len(self.s):
                return self.root
            if self.s.startswith("[[", self.i):
                self.i += 2
                keys = self.key()
                if not self.s.startswith("]]", self.i):
                    self.err("expected ]]")
                self.i += 2
                parent = self._walk(self.root, keys[:-1])
                arr = parent.setdefault(keys[-1], [])
                if not isinstance(arr, list):
                    self.err("not an array of tables")
                current = {}
                arr.append(current)
                self.eol()
            elif self.peek() == "[":
                self.i += 1
                keys = self.key()
                if self.peek() != "]":
                    self.err("expected ]")
                self.i += 1
                current = self._walk(self.root, keys)
                self.eol()
            else:
                keys = self.key()
                if self.peek() != "=":
                    self.err("expected =")
                self.i += 1
                self.ws()
                val = self.value()
                tgt = self._walk(current, keys[:-1])
                tgt[keys[-1]] = val
                self.eol()

    def _walk(self, d, keys):
        for k in keys:
            nxt = d.get(k)
            if nxt is None:
                nxt = d[k] = {}
            elif isinstance(nxt, list):
                nxt = nxt[-1]
            if not isinstance(nxt, dict):
                self.err("key %r is not a table" % k)
            d = nxt
        return d

    def key(self):
        parts = []
        while True:
            self.ws()
            c = self.peek()
            if c == '"':
                parts.append(self.basic())
            elif c == "'":
                parts.append(self.literal())
            else:
                m = _BARE.match(self.s, self.i)
                if not m:
                    self.err("bad key")
                parts.append(m.group(0))
                self.i = m.end()
            self.ws()
            if self.peek() == ".":
                self.i += 1
                continue
            return parts

    def basic(self):
        self.i += 1
        out = []
        while True:
            c = self.peek()
            if c == "":
                self.err("unterminated string")
            if c == '"':
                self.i += 1
                return "".join(out)
            if c == "\\":
                out.append(self.escape())
                continue
            if c == "\n":
                self.err("newline in string")
            out.append(c)
            self.i += 1

    def escape(self):
        self.i += 1
        c = self.peek()
        if c in _ESC:
            self.i += 1
            return _ESC[c]
        if c in ("u", "U"):
            n = 4 if c == "u" else 8
            h = self.s[self.i + 1 : self.i + 1 + n]
            self.i += 1 + n
            return chr(int(h, 16))
        self.err("bad escape")

    def literal(self):
        j = self.s.find("'", self.i + 1)
        if j == -1 or "\n" in self.s[self.i : j]:
            self.err("unterminated literal string")
        v = self.s[self.i + 1 : j]
        self.i = j + 1
        return v

    def ml_basic(self):
        self.i += 3
        if self.peek() == "\n":
            self.i += 1
        out = []
        while True:
            if self.s.startswith('"""', self.i):
                extra = 0
                while self.peek(3 + extra) == '"' and extra < 2:
                    extra += 1
                out.append('"' * extra)
                self.i += 3 + extra
                return "".join(out)
            c = self.peek()
            if c == "":
                self.err("unterminated string")
            if c == "\\":
                nxt = self.peek(1)
                if nxt in (" ", "\t", "\n"):
                    j = self.i + 1
                    while j < len(self.s) and self.s[j] in " \t\n":
                        j += 1
                    self.i = j
                    continue
                out.append(self.escape())
                continue
            out.append(c)
            self.i += 1

    def ml_literal(self):
        self.i += 3
        if self.peek() == "\n":
            self.i += 1
        j = self.s.find("'''", self.i)
        if j == -1:
            self.err("unterminated string")
        while self.s.startswith("'", j + 3) and j + 3 < len(self.s):
            j += 1
        v = self.s[self.i : j]
        self.i = j + 3
        return v

    def value(self):
        c = self.peek()
        if self.s.startswith('"""', self.i):
            return self.ml_basic()
        if self.s.startswith("'''", self.i):
            return self.ml_literal()
        if c == '"':
            return self.basic()
        if c == "'":
            return self.literal()
        if c == "[":
            self.i += 1
            arr = []
            while True:
                self.ws_nl()
                if self.peek() == "]":
                    self.i += 1
                    return arr
                arr.append(self.value())
                self.ws_nl()
                if self.peek() == ",":
                    self.i += 1
                    continue
                self.ws_nl()
                if self.peek() == "]":
                    self.i += 1
                    return arr
                self.err("expected , or ]")
        if c == "{":
            self.i += 1
            tbl = {}
            self.ws()
            if self.peek() == "}":
                self.i += 1
                return tbl
            while True:
                keys = self.key()
                if self.peek() != "=":
                    self.err("expected =")
                self.i += 1
                self.ws()
                self._walk(tbl, keys[:-1])[keys[-1]] = self.value()
                self.ws()
                if self.peek() == ",":
                    self.i += 1
                    continue
                if self.peek() == "}":
                    self.i += 1
                    return tbl
                self.err("expected , or }")
        m = _ATOM.match(self.s, self.i)
        if not m:
            self.err("bad value")
        tok = m.group(0).rstrip()
        self.i = m.start() + len(tok)
        if tok == "true":
            return True
        if tok == "false":
            return False
        t = tok.replace("_", "")
        try:
            if t[:2] in ("0x", "0o", "0b"):
                return int(t, 0)
            return int(t)
        except ValueError:
            pass
        try:
            return float(t)
        except ValueError:
            return tok
