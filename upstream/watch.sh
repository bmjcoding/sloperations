#!/usr/bin/env bash
# Reports upstream changes to the skills this repo ports by hand.
#
# For each entry in upstream/watch.json it compares the watched paths at the
# pinned commit with the branch head, through the GitHub API. It keeps one issue
# open per entry while they differ and closes it once the pin catches up. It
# never downloads or runs upstream code, and never changes this repository.
#
#   upstream/watch.sh              update issues (needs gh, jq and issue access)
#   DRY_RUN=1 upstream/watch.sh    print what it would do
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
config="$here/watch.json"
label=upstream-watch
dry=${DRY_RUN:-0}
status=0

# object_id REPO REF PATH: the git id of the file or folder at REF; empty if absent.
object_id() {
  # shellcheck disable=SC2016  # GraphQL variables, not shell
  gh api graphql \
    -f query='query($o:String!,$n:String!,$e:String!){repository(owner:$o,name:$n){object(expression:$e){oid}}}' \
    -f o="${1%%/*}" -f n="${1#*/}" -f e="$2:$3" --jq '.data.repository.object.oid // ""'
}

open_issue() { # TITLE-PREFIX: number of the open issue with that prefix, if any
  gh issue list --label "$label" --state open --limit 200 --json number,title \
    --jq "map(select(.title | startswith(\"$1\"))) | .[0].number // empty"
}

# Check the whole file's shape first, so a malformed entry can never read as "unchanged".
if ! jq -e '
  type == "array" and length > 0 and (map(.name) | unique | length) == length and
  all(.[]; type == "object" and
    ([.name, .repo, .branch, .pin, .local] | all(type == "string")) and
    (.paths | type == "array" and length > 0 and all(type == "string")))
' "$config" >/dev/null; then
  echo "watch.json: expected a non-empty list of entries with unique string name, repo, branch, pin and local, and a non-empty list of string paths" >&2
  exit 1
fi

count=$(jq 'length' "$config")
for ((i = 0; i < count; i++)); do
  name=$(jq -r ".[$i].name" "$config")
  repo=$(jq -r ".[$i].repo" "$config")
  branch=$(jq -r ".[$i].branch" "$config")
  pin=$(jq -r ".[$i].pin" "$config")
  local_dir=$(jq -r ".[$i].local" "$config")
  if ! [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ && "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ &&
          "$branch" =~ ^[A-Za-z0-9._/-]+$ && "$pin" =~ ^[0-9a-f]{40}$ && "$local_dir" =~ ^[A-Za-z0-9._/-]+$ ]]; then
    echo "watch.json entry $i: invalid name, repo, branch, pin or local" >&2
    status=1
    continue
  fi

  paths=$(jq -er ".[$i].paths[]" "$config")
  head=$(gh api "repos/$repo/commits/$branch" --jq .sha)
  since=$(gh api "repos/$repo/commits/$pin" --jq '.commit.committer.date[0:10]')
  changed=()
  valid=1
  while IFS= read -r path; do
    if ! [[ "$path" =~ ^[A-Za-z0-9._/-]+$ ]]; then
      echo "$name: invalid path in watch.json" >&2
      valid=0
      continue
    fi
    old=$(object_id "$repo" "$pin" "$path")
    new=$(object_id "$repo" "$head" "$path")
    if [ -z "$old" ] && [ -z "$new" ]; then
      echo "$name: $path exists at neither the pin nor the head of $repo" >&2
      valid=0
    elif [ "$old" != "$new" ]; then
      changed+=("$path")
    fi
  done <<< "$paths"
  if [ "$valid" = 0 ]; then
    # A bad path says nothing about upstream, so leave this entry's issue alone.
    echo "$name: skipped; fix its paths in watch.json" >&2
    status=1
    continue
  fi

  prefix="$name: upstream changed"
  if [ ${#changed[@]} -eq 0 ]; then
    echo "$name: unchanged since ${pin:0:7}"
    if [ "$dry" = 1 ]; then continue; fi
    number=$(open_issue "$prefix")
    if [ -n "$number" ]; then
      gh issue close "$number" --comment "The watched paths match the pin \`${pin:0:7}\` again." >/dev/null
    fi
    continue
  fi

  title="$prefix (${head:0:7})"
  body=$(
    echo "<!-- upstream-watch:$name -->"
    echo "\`$repo\` changed ${#changed[@]} watched path(s) since the pin \`${pin:0:7}\`:"
    echo
    for path in "${changed[@]}"; do
      echo "- [\`$path\`](https://github.com/$repo/commits/$head/$path?since=$since)"
    done
    echo
    echo "Port what matters into \`$local_dir\` by hand. Then set \`pin\` for \`$name\` in \`upstream/watch.json\` to:"
    echo
    echo '```'
    echo "$head"
    echo '```'
    echo
    echo "This issue closes on the next run after the pin is updated."
  )
  echo "$name: ${#changed[@]} path(s) changed since ${pin:0:7}: ${changed[*]}"
  if [ "$dry" = 1 ]; then
    printf '%s\n\n%s\n' "would open or update: $title" "$body"
    continue
  fi
  gh label create "$label" --force --color 5319e7 --description "Upstream changes to port by hand" >/dev/null
  number=$(open_issue "$prefix")
  if [ -n "$number" ]; then
    gh issue edit "$number" --title "$title" --body "$body" >/dev/null
  else
    gh issue create --label "$label" --title "$title" --body "$body" >/dev/null
  fi
done
exit "$status"
