#!/bin/sh
# Copy install.sh and every skill's SKILL.md into a temp checkout, run that copy against throwaway
# HOME directories, and check what it links, refuses and leaves. This checkout is only read.
# shellcheck disable=SC2016,SC2034 # check bodies are single-quoted for eval; run sets code
set -u
here=$(CDPATH='' cd -P -- "$(dirname -- "$0")" && pwd -P)
origin=$(CDPATH='' cd -P -- "$here/../.." && pwd -P)
work=$(mktemp -d) || exit 1
[ -n "$work" ] || exit 1
trap 'chmod -R u+w "$work" 2>/dev/null; rm -rf "$work"' EXIT
work=$(CDPATH='' cd -P -- "$work" && pwd -P) || exit 1
failures=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1" >&2; failures=$((failures + 1)); fi; }

names=$(for f in "$origin"/skills/*/SKILL.md; do [ -f "$f" ] || continue; d=${f%/SKILL.md}; echo "${d##*/}"; done)
[ -n "$names" ] || { echo "no skills found under $origin/skills" >&2; exit 2; }
repo=$work/copy; mkdir -p "$repo"; cp "$origin/install.sh" "$repo/"
for n in $names; do mkdir -p "$repo/skills/$n"; cp "$origin/skills/$n/SKILL.md" "$repo/skills/$n/"; done
install=$repo/install.sh
find "$repo/skills" | sort >"$work/before"
first=$(echo "$names" | head -n 1)
second=$(echo "$names" | sed -n 2p)
dests='.agents/skills .claude/skills'

all_linked() { # HOME: every skill is linked from both destinations to its folder in the copy
  for n in $names; do
    for d in $dests; do
      [ -L "$1/$d/$n" ] && [ "$(readlink "$1/$d/$n")" = "$repo/skills/$n" ] || return 1
    done
  done
}
run() { h=$1; shift; HOME=$h "$install" "$@" >"$work/out" 2>"$work/err"; code=$?; }
untouched() { [ ! -e "$1/.agents" ] && [ ! -e "$1/.claude" ]; }
fresh() { h=$work/$1; mkdir -p "$h"; }

fresh fresh; run "$h"
check "fresh HOME: exits 0 and links every skill from both destinations" '[ "$code" = 0 ] && all_linked "$h"'
run "$h"
check "second run: exits 0, reports every link ok, creates none" '[ "$code" = 0 ] && ! grep -q "^link:" "$work/out" && [ "$(grep -c "^ok:" "$work/out")" = "$(( $(echo "$names" | wc -l) * 2 ))" ] && all_linked "$h"'

fresh dry; run "$h" --dry-run
for n in $names; do for d in $dests; do echo "link: $h/$d/$n -> $repo/skills/$n"; done; done | sort >"$work/want"
sort "$work/out" >"$work/got"
check "dry run: exits 0, prints every planned link, creates nothing" '[ "$code" = 0 ] && cmp -s "$work/want" "$work/got" && untouched "$h"'

fresh relative; mkdir -p "$h/.claude/skills"; ln -s "$repo" "$work/alias"
ln -s "../../../alias/skills/$first" "$h/.claude/skills/$first"
check "relative-link fixture resolves to the skill" '[ "$(cd -P "$h/.claude/skills/$first" && pwd -P)" = "$repo/skills/$first" ]'
run "$h"
check "relative link into the skill: kept as ok, other destination linked" '[ "$code" = 0 ] && grep -Fxq "ok: $h/.claude/skills/$first" "$work/out" && [ "$(readlink "$h/.agents/skills/$first")" = "$repo/skills/$first" ]'

if [ "$work/COPY" -ef "$repo" ]; then
  fresh case; mkdir -p "$h/.claude/skills"; ln -s "$work/COPY/skills/$first" "$h/.claude/skills/$first"
  run "$h" --dry-run
  check "case-variant link, dry run: ok, creates nothing" '[ "$code" = 0 ] && grep -Fxq "ok: $h/.claude/skills/$first" "$work/out" && [ ! -e "$h/.agents" ]'
  run "$h"
  check "case-variant link: kept as ok, other destination linked" '[ "$code" = 0 ] && grep -Fxq "ok: $h/.claude/skills/$first" "$work/out" && [ -L "$h/.agents/skills/$first" ]'
else echo "skip case-variant link: this filesystem is case-sensitive"; fi

# refuses NAME TARGET: a link at ~/.claude/skills/<first> to TARGET is refused and left alone
refuses() {
  t=$2; fresh "$1"; mkdir -p "$h/.claude/skills"; ln -s "$t" "$h/.claude/skills/$first"; run "$h"
  check "$1 link: refused by path, unchanged, other destination absent, other skills linked" '[ "$code" != 0 ] && grep -Fq "$h/.claude/skills/$first" "$work/err" && [ "$(readlink "$h/.claude/skills/$first")" = "$t" ] && [ ! -e "$h/.agents/skills/$first" ] && [ ! -L "$h/.agents/skills/$first" ] && [ "$(readlink "$h/.agents/skills/$second")" = "$repo/skills/$second" ]'
}
mkdir -p "$work/outside/$first" "$work/through"; ln -s "$work/outside" "$work/through/dir"
check "traversal fixture exists" '[ -d "$repo/../outside/$first" ]'
refuses other-skill "$repo/skills/$second"
refuses traversal "$repo/../outside/$first"
refuses outside "$work/through/dir/$first"
refuses broken "$work/missing"

echo keep >"$work/keep"
for kind in folder file; do
  fresh "$kind"; mkdir -p "$h/.claude/skills"; p=$h/.claude/skills/$first
  if [ "$kind" = folder ]; then mkdir "$p"; p=$p/SKILL.md; fi
  cp "$work/keep" "$p"; run "$h"
  check "existing $kind: refused by path, bytes kept, other destination absent, other skills linked" '[ "$code" != 0 ] && grep -Fq "$h/.claude/skills/$first" "$work/err" && [ ! -L "$h/.claude/skills/$first" ] && cmp -s "$work/keep" "$p" && [ ! -e "$h/.agents/skills/$first" ] && [ "$(readlink "$h/.agents/skills/$second")" = "$repo/skills/$second" ]'
done

fresh args
for a in "--force" "'' --dry-run" "--dry-run extra"; do
  eval "run \"\$h\" $a"
  check "arguments $a: exits 2, changes nothing" '[ "$code" = 2 ] && untouched "$h"'
done
mkdir "$work/cwd"
(cd "$work/cwd" && unset HOME && "$install" >/dev/null 2>&1); code=$?
check "unset HOME: exits 2" '[ "$code" = 2 ]'
for v in "" rel; do
  (cd "$work/cwd" && HOME=$v "$install" --dry-run >/dev/null 2>&1); code=$?
  check "HOME='$v': exits 2, creates nothing" '[ "$code" = 2 ] && [ -z "$(ls -A "$work/cwd")" ]'
done

fresh blocked; echo x >"$h/.claude"
for a in --dry-run ""; do
  eval "run \"\$h\" $a"
  check "file at ~/.claude (${a:-apply}): exits 1, names it, creates nothing" '[ "$code" = 1 ] && grep -Fq "$h/.claude" "$work/err" && [ ! -e "$h/.agents" ]'
done
if [ "$(id -u)" -ne 0 ]; then
  fresh readonly; mkdir -p "$h/.claude/skills"; chmod 555 "$h/.claude/skills"
  for a in --dry-run ""; do
    eval "run \"\$h\" $a"
    check "read-only ~/.claude/skills (${a:-apply}): exits 1, names it, creates nothing" '[ "$code" = 1 ] && grep -Fq "$h/.claude/skills" "$work/err" && [ ! -e "$h/.agents" ]'
  done
  chmod 755 "$h/.claude/skills"
fi
fresh lnfail; mkdir "$work/failbin"; real_ln=$(command -v ln)
printf '#!/bin/sh\ncase "$*" in *.agents/skills*) exit 1 ;; esac\nexec %s "$@"\n' "$real_ln" >"$work/failbin/ln"
chmod +x "$work/failbin/ln"
HOME=$h PATH="$work/failbin:$PATH" "$install" >/dev/null 2>&1; code=$?
check "first link fails: exits 1, second destination left empty" '[ "$code" = 1 ] && [ -z "$(ls -A "$h/.claude/skills")" ]'

fresh launcher; mkdir "$work/bin"; ln -s "$install" "$work/bin/run-install"
HOME=$h PATH="$work/bin:$PATH" run-install >/dev/null 2>&1; code=$?
check "launched through a symlink on PATH: links every skill" '[ "$code" = 0 ] && all_linked "$h"'
fresh cwd-launch; (cd "$repo" && HOME=$h sh install.sh >/dev/null 2>&1); code=$?
check "sh install.sh from the checkout: links every skill" '[ "$code" = 0 ] && all_linked "$h"'

fx=$work/checkout; mkdir -p "$fx/skills/real" "$work/ext"; cp "$install" "$fx/"
echo x >"$fx/skills/real/SKILL.md"; echo x >"$work/ext/SKILL.md"; ln -s "$work/ext" "$fx/skills/ext"
fresh fixture; HOME=$h "$fx/install.sh" >"$work/out" 2>"$work/err"; code=$?
check "symlinked skill folder: refused, not linked, real skill linked" '[ "$code" = 1 ] && grep -Fq "$fx/skills/ext" "$work/err" && [ ! -e "$h/.claude/skills/ext" ] && [ ! -L "$h/.claude/skills/ext" ] && [ -L "$h/.claude/skills/real" ]'
mkdir "$work/noskills"; cp "$install" "$work/noskills/"
fresh none; HOME=$h "$work/noskills/install.sh" >/dev/null 2>&1; code=$?
check "no skills: exits 1" '[ "$code" = 1 ]'

fresh concurrent
HOME=$h "$install" >/dev/null 2>&1 & p1=$!
HOME=$h "$install" >/dev/null 2>&1 & p2=$!
wait "$p1"; c1=$?; wait "$p2"; c2=$?; find "$repo/skills" | sort >"$work/after"
check "two concurrent runs: both exit 0, all linked; no run changed the checkout's skills" '[ "$c1" = 0 ] && [ "$c2" = 0 ] && cmp -s "$work/before" "$work/after" && all_linked "$h"'

[ "$failures" = 0 ] || { echo "$failures check(s) failed" >&2; exit 1; }
echo "all checks passed"
