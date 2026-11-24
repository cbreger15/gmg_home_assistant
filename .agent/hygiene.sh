#!/usr/bin/env bash
# hygiene.sh [--dry-run] [--quiet]: clean up finished work so the repo stays small and fast.
#
# Deletes only what is provably safe (nothing that exists only on this PC):
#   - remote-tracking refs for branches deleted on GitHub    (git fetch --prune)
#   - stale worktree registrations                             (git worktree prune)
#   - worktrees that are clean, unlocked, idle for HYGIENE_KEEP_HOURS, and whose
#     commits are all on GitHub (removing one also frees its node_modules)
#   - local branches whose PR was merged, or whose upstream is gone and whose
#     commits are all on GitHub
#   - GitHub branches whose PR was merged and that GitHub did not auto-delete
# Reports, never deletes: dirty worktrees, branches with unpushed commits, GitHub
# branches with no merged PR. Never touches the base branch, the current branch,
# or any branch with an open PR.
#
# Works in any git repo with a GitHub remote. Uses .agent/profile.env when present.
set -uo pipefail

DRY=0; QUIET=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --quiet) QUIET=1 ;;
    *) echo "usage: hygiene.sh [--dry-run] [--quiet]" >&2; exit 2 ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel)" || exit 2
cd "$ROOT" || exit 2
# shellcheck source=/dev/null
[[ -f .agent/profile.env ]] && source .agent/profile.env
git fetch --prune --quiet origin || { echo "hygiene: git fetch failed; nothing cleaned" >&2; exit 1; }
git worktree prune

BASE="${BASE_BRANCH:-$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD | sed 's#^origin/##')}"
[[ -n "$BASE" ]] || { echo "hygiene: cannot tell the base branch; set BASE_BRANCH" >&2; exit 2; }
KEEP_HOURS="${HYGIENE_KEEP_HOURS:-24}"
REPO="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)}"
CURRENT="$(git rev-parse --abbrev-ref HEAD)"

say() { (( QUIET )) || echo "$@"; }
act() { if (( DRY )); then say "  would run: $*"; else "$@" >/dev/null 2>&1; fi; }

# Merged and open PRs from GitHub: "branch headSha" lines, and open branch names.
merged_prs=""; open_prs=""
if [[ -n "$REPO" ]]; then
  merged_prs="$(gh pr list --repo "$REPO" --state merged --limit 1000 \
    --json headRefName,headRefOid --jq '.[] | "\(.headRefName) \(.headRefOid)"' 2>/dev/null)"
  open_prs="$(gh pr list --repo "$REPO" --state open --limit 1000 \
    --json headRefName --jq '.[].headRefName' 2>/dev/null)"
else
  say "hygiene: gh is unavailable, so merged-PR checks are skipped (only pushed-and-pruned work is cleaned)."
fi

has_open_pr() { grep -qxF -- "$1" <<<"$open_prs"; }

# merged_tip <branch> <sha>: true when a merged PR for <branch> contains <sha>.
merged_tip() {
  local oid
  while read -r name oid; do
    [[ "$name" == "$1" ]] || continue
    [[ "$oid" == "$2" ]] && return 0
    git merge-base --is-ancestor "$2" "$oid" 2>/dev/null && return 0
  done <<<"$merged_prs"
  return 1
}

# on_github <sha>: true when every commit up to <sha> is on some GitHub branch.
on_github() { [[ -z "$(git rev-list -n 1 "$1" --not --remotes=origin 2>/dev/null)" ]]; }

newest_mtime() {
  local newest=0 t f
  for f in "$@"; do
    [[ -e "$f" ]] || continue
    t="$(stat -c %Y "$f" 2>/dev/null || echo 0)"
    (( t > newest )) && newest=$t
  done
  echo "$newest"
}

removed_wt=0; removed_local=0; removed_remote=0
kept=()

# 1. Worktrees (not the main checkout).
main_wt="$(git worktree list --porcelain | awk 'NR==1 {sub(/^worktree /, ""); print}')"
while IFS=$'\t' read -r path sha branch locked; do
  [[ -z "$path" || "$path" == "$main_wt" ]] && continue
  [[ -d "$path" ]] || continue
  name="${branch:-detached ${sha:0:9}}"
  if [[ "$locked" == "locked" ]]; then kept+=("worktree $path ($name): locked"); continue; fi
  if [[ -n "$branch" ]] && has_open_pr "$branch"; then kept+=("worktree $path ($name): open PR"); continue; fi
  # Read the last-used time before anything else touches the worktree's index.
  gitdir="$(git -C "$path" rev-parse --absolute-git-dir 2>/dev/null)"
  last="$(newest_mtime "$gitdir/index" "$gitdir/HEAD" "$gitdir/logs/HEAD")"
  if [[ -n "$(git --no-optional-locks -C "$path" status --porcelain 2>/dev/null)" ]]; then
    kept+=("worktree $path ($name): uncommitted changes"); continue
  fi
  if (( $(date +%s) - last < KEEP_HOURS * 3600 )); then
    kept+=("worktree $path ($name): used in the last ${KEEP_HOURS}h"); continue
  fi
  if on_github "$sha" || { [[ -n "$branch" ]] && merged_tip "$branch" "$sha"; }; then
    act git worktree remove "$path" && removed_wt=$((removed_wt + 1))
  else
    kept+=("worktree $path ($name): commits not on GitHub")
  fi
done < <(git worktree list --porcelain | awk '
  /^worktree / { if (p != "") print p "\t" s "\t" b "\t" l; p = substr($0, 10); s = ""; b = ""; l = "" }
  /^HEAD /     { s = $2 }
  /^branch /   { b = $2; sub(/^refs\/heads\//, "", b) }
  /^locked/    { l = "locked" }
  END          { if (p != "") print p "\t" s "\t" b "\t" l }')
(( DRY )) || git worktree prune

# 2. Local branches.
in_worktree="$(git worktree list --porcelain | awk '/^branch / {sub(/^branch refs\/heads\//, ""); print}')"
while read -r branch sha upstream track; do
  [[ "$branch" == "$BASE" || "$branch" == "$CURRENT" ]] && continue
  grep -qxF -- "$branch" <<<"$in_worktree" && continue
  has_open_pr "$branch" && continue
  if merged_tip "$branch" "$sha" || { [[ -n "$upstream" && "$track" == "[gone]" ]] && on_github "$sha"; }; then
    act git branch -D "$branch" && removed_local=$((removed_local + 1))
  elif ! on_github "$sha"; then
    if grep -q "^$branch " <<<"$merged_prs"; then
      kept+=("branch $branch: has commits added after its PR merged")
    else
      ahead="$(git rev-list --count "$sha" --not --remotes=origin)"
      kept+=("branch $branch: $ahead commit(s) only on this PC")
    fi
  fi
done < <(git for-each-ref refs/heads --format='%(refname:short) %(objectname) %(upstream:short) %(upstream:track)')

# 3. GitHub branches left behind by merged PRs.
no_pr=0
while read -r branch sha; do
  [[ "$branch" == "HEAD" || "$branch" == "$BASE" ]] && continue
  has_open_pr "$branch" && continue
  if merged_tip "$branch" "$sha"; then
    act git push origin --delete "$branch" && removed_remote=$((removed_remote + 1))
  else
    no_pr=$((no_pr + 1))
  fi
done < <(git for-each-ref refs/remotes/origin --format='%(refname:strip=3) %(objectname)')

(( DRY )) || git gc --auto --quiet

prefix=""; (( DRY )) && prefix="(dry run) "
say "${prefix}Hygiene: removed $removed_wt worktree(s), $removed_local local branch(es), $removed_remote GitHub branch(es)."
if (( ${#kept[@]} )); then
  say "Kept, needs a look:"
  printf '  - %s\n' "${kept[@]}" | head -20
fi
(( no_pr )) && say "  - $no_pr GitHub branch(es) have no merged PR (left alone; review them on GitHub)."
exit 0
