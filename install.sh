#!/bin/sh
# Link each skill in skills/ into ~/.agents/skills and ~/.claude/skills.
#   ./install.sh [--dry-run]   create the missing links; --dry-run prints them and changes nothing
#
# Links that already resolve to this checkout's skill are kept. Any other file, folder or link at a
# destination is refused by path, and that skill is left untouched. Nothing is replaced or deleted.
set -eu
case "${HOME-}" in /*) ;; *) echo "HOME must be an absolute path" >&2; exit 2 ;; esac
[ -d "$HOME" ] || { echo "HOME is not a directory: $HOME" >&2; exit 2; }
case "$#:${1-}" in
  0:) dry=0 ;;
  1:--dry-run) dry=1 ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac
case "$0" in
  */*) script=$0 ;;
  *) if [ -f "$0" ]; then script=./$0; else script=$(command -v -- "$0") || { echo "cannot locate script: $0" >&2; exit 1; }; fi ;;
esac
while [ -L "$script" ]; do
  link=$(readlink "$script")
  case "$link" in /*) script=$link ;; *) script=$(dirname -- "$script")/$link ;; esac
done
repo=$(CDPATH='' cd -P -- "$(dirname -- "$script")" && pwd -P)
for p in "$HOME/.agents" "$HOME/.agents/skills" "$HOME/.claude" "$HOME/.claude/skills"; do
  if [ -e "$p" ] || [ -L "$p" ]; then
    [ -d "$p" ] || { echo "refused: $p exists and is not a directory" >&2; exit 1; }
  fi
done
is_target_link() { [ -L "$1" ] && [ "$1" -ef "$2" ]; } # DEST is a link to the folder SRC
can_write() { # DIR: the nearest existing parent of DIR is a writable folder; sets p to it
  p=$1; while [ ! -e "$p" ] && [ ! -L "$p" ]; do p=${p%/*}; [ -n "$p" ] || p=/; done
  [ -d "$p" ] && [ -w "$p" ] && [ -x "$p" ]
}
status=0 found=0 dirs_ready=0
for src in "$repo"/skills/*/; do
  src=${src%/}
  name=${src##*/}
  [ -f "$src/SKILL.md" ] || continue
  real_src=$(CDPATH='' cd -P -- "$src" 2>/dev/null && pwd -P) || { echo "refused: cannot resolve $src" >&2; status=1; continue; }
  if [ -L "$src" ] || [ "$real_src" != "$repo/skills/$name" ]; then
    echo "refused: $src resolves outside $repo/skills" >&2; status=1; continue
  fi
  found=$((found + 1))
  refused=0
  for dir in "$HOME/.agents/skills" "$HOME/.claude/skills"; do
    dest=$dir/$name
    if is_target_link "$dest" "$src"; then :
    elif [ -L "$dest" ]; then echo "refused: $dest links to $(readlink "$dest"), not $src" >&2; refused=1
    elif [ -e "$dest" ]; then echo "refused: $dest exists and is not a link" >&2; refused=1
    elif ! can_write "$dir"; then echo "refused: $dest ($p is not writable)" >&2; refused=1
    fi
  done
  if [ "$refused" = 1 ]; then status=1; continue; fi
  for dir in "$HOME/.agents/skills" "$HOME/.claude/skills"; do
    dest=$dir/$name
    if is_target_link "$dest" "$src"; then echo "ok: $dest"; continue; fi
    echo "link: $dest -> $src"
    [ "$dry" = 1 ] && continue
    if [ "$dirs_ready" = 0 ]; then mkdir -p -- "$HOME/.agents/skills" "$HOME/.claude/skills"; dirs_ready=1; fi
    # ln into the folder: an entry that appeared since the check makes it fail, never followed.
    if ! err=$(ln -s -- "$src" "$dir/" 2>&1); then
      if ! is_target_link "$dest" "$src"; then echo "refused: $dest ($err)" >&2; status=1; break; fi
    fi
  done
done
[ "$found" -gt 0 ] || { echo "no skills found under $repo/skills" >&2; exit 1; }
exit "$status"
