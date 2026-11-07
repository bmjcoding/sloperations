"""Path classification: test / support / production / docs / config /
generated / other (plus `ignore` from config). First match wins:
config globs, config testGlobs, then the built-in heuristics below."""

import os
import re

from . import detect
from .core import glob_match

CLASS_ORDER = ("test", "support", "generated", "docs", "config", "production")
REPORT_CLASSES = ("production", "test", "support", "docs", "config", "generated", "other")

SOURCE_EXTS = detect.JS_EXTS + detect.PY_EXTS + (
    ".vue", ".svelte", ".astro", ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".rb",
    ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".m", ".mm", ".cs", ".swift", ".dart",
    ".ex", ".exs", ".lua", ".sh", ".bash", ".zsh", ".css", ".scss", ".sass", ".less",
    ".html", ".sql", ".graphql", ".gql", ".proto", ".zig",
)
LOCK_NAMES = {
    "pnpm-lock.yaml", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "bun.lock",
    "bun.lockb", "uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock", "Cargo.lock",
    "Gemfile.lock", "composer.lock", "go.sum",
}
GENERATED_SEGMENTS = {"__snapshots__", "__generated__", "generated", "dist", "coverage"}
FIXTURE_SEGMENTS = {
    "fixtures", "fixture", "__fixtures__", "__mocks__", "mocks", "__helpers__", "testdata",
    "test-fixtures", "test-utils", "test_utils", "testutils", "test-helpers", "test_helpers",
    "test-support", "test_support",
}
TEST_DIR_SEGMENTS = {"test", "tests", "__tests__", "spec", "specs", "e2e", "testing"}
SUPPORT_NAME = re.compile(
    r"^(conftest\.py|setup-?tests?\.[cm]?[jt]sx?|setupTests\.[cm]?[jt]sx?|"
    r"(vitest|jest|test)[.-]setup\.[cm]?[jt]sx?|test[-_]?utils?\.[a-z]+|test[-_]helpers?\.[a-z]+|"
    r"testutils\.[a-z]+|.*\.(fixtures?|mocks?|test-utils|test-helpers|stub|stubs)\.[a-z]+)$"
)
DOC_EXTS = (".md", ".mdx", ".rst", ".adoc", ".asciidoc", ".txt")
DOC_NAMES = re.compile(r"^(README|LICEN[CS]E|COPYING|CHANGELOG|CHANGES|HISTORY|NOTICE|AUTHORS|CONTRIBUTING|CODE_OF_CONDUCT|SECURITY)(\.[a-z]+)?$", re.I)
CONFIG_NAMES = {
    "package.json", "pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "pytest.ini",
    ".coveragerc", "mypy.ini", ".mypy.ini", "ruff.toml", ".ruff.toml", "Makefile",
    "Justfile", "justfile", ".gitignore", ".gitattributes", ".editorconfig", ".npmrc",
    ".nvmrc", ".node-version", ".python-version", ".tool-versions", "pnpm-workspace.yaml",
    "turbo.json", "nx.json", "lerna.json", "biome.json", "biome.jsonc", ".oxlintrc.json",
    ".oxfmtrc.json", ".oxfmtrc.jsonc", "knip.json", "knip.jsonc", ".knip.json",
    ".knip.jsonc", "bunfig.toml", ".babelrc", ".yarnrc", ".yarnrc.yml", ".test-audit.json",
    "Pipfile", "MANIFEST.in", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".dockerignore", ".prettierignore", ".eslintignore", "pyrightconfig.json", "jsconfig.json",
    "deno.json", "deno.jsonc", "renovate.json", ".pre-commit-config.yaml", "CODEOWNERS",
    "go.mod", "Cargo.toml", "Gemfile",
}
CONFIG_NAME_RX = re.compile(
    r"^(tsconfig.*\.json|requirements.*\.txt|\.env.*|Dockerfile\..*|\.eslintrc.*|\.prettierrc.*|"
    r".*\.config\.[cm]?[jt]s|.*\.config\.json|.*rc\.json|.*\.(toml|ini|cfg|ya?ml|properties))$"
)
CONFIG_DIRS = (".github/", ".gitlab/", ".circleci/", ".husky/", ".vscode/", ".devcontainer/", ".changeset/", ".config/", ".yarn/")


def _is_js_test(ctx, path, base):
    low = base.lower()
    if not low.endswith(detect.JS_EXTS):
        return False
    stem = low.rsplit(".", 1)[0]
    if re.search(r"\.(test|spec)(-d)?$", stem):
        return True
    root = detect.test_root(ctx, path, "js")
    runners = {x["runner"] for x in (root.runners_detected if root else [])} | ({root.runner} if root and root.runner else set())
    if "bun" in runners and re.search(r"_(test|spec)$", stem):
        return True
    if "jest" in runners and "/__tests__/" in "/" + path:
        return not SUPPORT_NAME.match(base) and not any(seg in FIXTURE_SEGMENTS for seg in path.split("/")[:-1])
    return False


def is_test(ctx, path):
    return classify(ctx, path) == "test"


def classify(ctx, path):
    key = ("cls", path)
    if key in ctx.cache:
        return ctx.cache[key]
    c = _classify(ctx, path)
    ctx.cache[key] = c
    return c


def _classify(ctx, path):
    cfg = ctx.config.get("classify", {})
    if glob_match(path, cfg.get("ignore")):
        return "ignore"
    for cls in CLASS_ORDER:
        if glob_match(path, cfg.get(cls)):
            return cls
    if glob_match(path, ctx.config.get("testGlobs")):
        return "test"
    base = os.path.basename(path)
    dirs = path.split("/")[:-1]
    low = base.lower()
    # generated
    if base in LOCK_NAMES or low.endswith((".min.js", ".min.css", ".map", ".snap", "_pb2.py", "_pb2.pyi", ".pb.go")):
        return "generated"
    if re.search(r"\.(generated|gen)\.[a-z]+$", low) or any(seg in GENERATED_SEGMENTS for seg in dirs):
        return "generated"
    # tests
    if low.endswith(".py") and detect.py_test_name(ctx, path) and not any(seg in FIXTURE_SEGMENTS for seg in dirs):
        return "test"
    if _is_js_test(ctx, path, base) and not any(seg in FIXTURE_SEGMENTS for seg in dirs):
        return "test"
    # support by name or fixture directory
    if SUPPORT_NAME.match(base) or any(seg in FIXTURE_SEGMENTS for seg in dirs):
        return "support"
    # docs
    if low.endswith(DOC_EXTS) and not re.match(r"^requirements.*\.txt$", low) or DOC_NAMES.match(base):
        return "docs"
    if (dirs and dirs[0] in ("docs", "doc")) or ("docs" in dirs and not low.endswith(SOURCE_EXTS)):
        return "docs"
    # config
    if base in CONFIG_NAMES or CONFIG_NAME_RX.match(base) or path.startswith(CONFIG_DIRS):
        return "config"
    # remaining files under test directories are support
    if any(seg in TEST_DIR_SEGMENTS for seg in dirs):
        return "support"
    if low.endswith(SOURCE_EXTS):
        return "production"
    return "other"


def test_files(ctx, scopes=None, discovery=True):
    """In-scope test files, sorted. scopes: repo-relative files/dirs.

    With discovery=True and no scopes this is default discovery, so pytest
    `testpaths` apply (as for a bare `pytest`); explicit scopes ignore them,
    as pytest does. discovery=False returns every classified test (owner
    mapping in `changed` must see tests outside `testpaths`)."""
    from . import pytestcfg

    out = []
    for f in ctx.files():
        if scopes and not any(f == s or s == "." or f.startswith(s.rstrip("/") + "/") for s in scopes):
            continue
        if classify(ctx, f) == "test":
            if discovery and not scopes and f.endswith(".py") and not pytestcfg.in_testpaths(ctx, f):
                continue
            out.append(f)
    return out


def is_binary(path):
    try:
        with open(path, "rb") as f:
            return b"\0" in f.read(8192)
    except OSError:
        return False


def count_lines(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return 0
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)
