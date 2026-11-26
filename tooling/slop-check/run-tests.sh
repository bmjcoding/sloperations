#!/bin/sh
# Run the slop-check suite under Node >= 24, once per available Python >= 3.9 (the emitter's floor).
set -u
here=$(CDPATH='' cd -P -- "$(dirname -- "$0")" && pwd -P)
major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null) || major=0
[ "$major" -ge 24 ] || { echo "needs Node >= 24 on PATH" >&2; exit 2; }
scripts="$here/../../skills/slop-cleanup/scripts"
[ -d "$scripts/node_modules/oxc-parser" ] || (cd "$scripts" && npm ci --no-audit --no-fund) || exit 2
status=0
ran=0
for py in /usr/bin/python3 python3.9 python3 python3.14; do
  exe=$(command -v "$py" 2>/dev/null) || continue
  real=$("$exe" -c 'import os, sys; print(os.path.realpath(sys.executable))' 2>/dev/null) || continue
  case " ${seen:-} " in *" $real "*) continue ;; esac
  seen="${seen:-} $real"
  "$exe" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || continue
  echo "== node $(node --version), $("$exe" --version 2>&1) ($exe)"
  SLOP_CHECK_PYTHON=$exe node --test "$@" "$here"/tests/*.test.mjs || status=1
  ran=$((ran + 1))
done
[ "$ran" -gt 0 ] || { echo "no Python >= 3.9 found" >&2; exit 2; }
exit $status
