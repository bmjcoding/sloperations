# test-audit CLI tests

Behavior tests for `skills/test-audit/scripts/test-audit` (reference:
`skills/test-audit/references/tooling.md`). Every test drives the real launcher
against a throwaway git repository built in a temp dir; nothing touches this
checkout. Standard library `unittest` only.

```sh
# from the repo root; the tool runs under the same interpreter as the suite
python3 -B -m unittest discover -s tooling/test-audit/tests -t tooling/test-audit/tests
/usr/bin/python3 -B -m unittest discover -s tooling/test-audit/tests -t tooling/test-audit/tests   # oldest supported (3.9)

# or both at once
sh tooling/test-audit/run-tests.sh

# opt-in: real pnpm/Vitest, Vite+, npm/Jest, Yarn Berry PnP, bun, uv/pytest (+xdist),
# ruff/mypy, knip/vulture installs (network, ~2-3 min); a missing toolchain skips its case
TEST_AUDIT_TOOLCHAINS=1 python3 -B -m unittest discover -s tooling/test-audit/tests -t tooling/test-audit/tests -p 'test_toolchains.py'
```

- **Hermetic git:** every fixture and tool git process runs with
  `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, a fixed identity,
  `commit.gpgsign=false`, and `core.hooksPath=/dev/null`, so host config,
  signing, and hooks never affect results (`ta_helpers.hermetic_git_env`).
- **Skips:** `PytestTest` needs `pytest` importable by the suite interpreter;
  its xdist case also needs `pytest-xdist`.
