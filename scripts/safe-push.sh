#!/usr/bin/env bash
# safe-push: push the commits of THIS agent worktree to origin/<branch>.
# Runs only in a linked worktree (never the shared checkout), only when the worktree is fully clean
# (no uncommitted or untracked files, no rebase/merge/cherry-pick in progress) and has no merge
# commits. Rebases onto origin, optionally runs a test, pushes, and retries only after losing a race.
# usage: scripts/safe-push.sh [--test "CMD"] [--branch main]
# exit: 0 ok / 2 refused or usage / 3 rebase failed / 4 test failed / 6 push failed / 130 interrupted
set -u
BRANCH=main; TEST_CMD=""
refuse() { echo "safe-push: $1" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --test) [ $# -ge 2 ] || refuse "--test needs a command"; TEST_CMD="$2"; shift 2 ;;
    --branch) [ $# -ge 2 ] || refuse "--branch needs a name"; BRANCH="$2"; shift 2 ;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) refuse "unknown arg $1" ;;
  esac
done
TRIES="${SAFE_PUSH_TRIES:-5}"; BACKOFF="${SAFE_PUSH_BACKOFF:-1}"
case "$TRIES$BACKOFF" in *[!0-9]*|'') refuse "SAFE_PUSH_TRIES and SAFE_PUSH_BACKOFF must be whole numbers" ;; esac
[ "$TRIES" -ge 1 ] || refuse "SAFE_PUSH_TRIES must be at least 1"
case "$BRANCH" in -*) refuse "bad branch name: $BRANCH" ;; esac
git check-ref-format --branch "$BRANCH" >/dev/null 2>&1 || refuse "bad branch name: $BRANCH"
[ -z "${GIT_DIR:-}${GIT_WORK_TREE:-}" ] || refuse "GIT_DIR/GIT_WORK_TREE are set; run from inside your worktree instead"

git rev-parse --git-dir >/dev/null 2>&1 || refuse "not inside a git repository"
git remote get-url origin >/dev/null 2>&1 || refuse "no 'origin' remote"
GITDIR="$(cd "$(git rev-parse --absolute-git-dir)" && pwd -P)"
COMMON="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"
[ "$GITDIR" != "$COMMON" ] || refuse "refusing to push from the shared checkout; work in your own worktree (claude --worktree <name>, or git worktree add -b agent/<name> <dir> origin/$BRANCH)"
UPSTREAM="refs/remotes/origin/$BRANCH"

in_progress() {
  for f in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG; do
    [ -e "$GITDIR/$f" ] && { echo "$f"; return 0; }
  done
  return 1
}
check_clean() {
  op="$(in_progress)" && refuse "a git operation is in progress ($op); finish or abort it first"
  [ -z "$(git status --porcelain)" ] || refuse "worktree has uncommitted or untracked files; commit, remove or ignore them first"
}

PHASE=""
on_signal() {
  case "$PHASE" in
    rebase) [ -e "$GITDIR/rebase-merge" ] && git rebase --abort >/dev/null 2>&1 ;;
    push)
      if [ "$(git ls-remote origin "refs/heads/$BRANCH" 2>/dev/null | cut -f1)" = "$(git rev-parse HEAD)" ]; then
        echo "safe-push: pushed to origin/$BRANCH ($(git rev-parse --short HEAD)) before the interrupt"; exit 0
      fi ;;
  esac
  echo "safe-push: interrupted; nothing pushed" >&2; exit 130
}
trap on_signal INT TERM
export SAFE_PUSH_PID=$$

# After a push, bring the shared checkout (where hooks and scripts run) up to date - fast-forward
# only. git refuses without touching anything when it has local commits or changes in the way.
sync_shared() {
  shared="$(git worktree list --porcelain | sed -n '1s/^worktree //p')"
  [ -d "$shared" ] || return 0
  b="$(git -C "$shared" symbolic-ref --short -q HEAD)"
  if [ "$b" != "$BRANCH" ]; then
    echo "safe-push: shared checkout not updated (it is on ${b:-a detached HEAD}, not $BRANCH)"; return 0
  fi
  if git -C "$shared" merge -q --ff-only "$UPSTREAM" >/dev/null 2>&1; then
    echo "safe-push: updated shared checkout $shared to $(git -C "$shared" rev-parse --short HEAD)"
  else
    echo "safe-push: shared checkout not updated (it has local commits or changes in the way; left as is)"
  fi
}

try=1
while :; do
  check_clean
  git fetch -q origin -- "+refs/heads/$BRANCH:$UPSTREAM" || { echo "safe-push: fetch of $BRANCH failed" >&2; exit 6; }
  ahead="$(git rev-list "$UPSTREAM..HEAD")" || { echo "safe-push: cannot compare with $UPSTREAM" >&2; exit 6; }
  [ -n "$ahead" ] || { echo "safe-push: nothing to push"; exit 0; }
  [ -z "$(git rev-list --merges "$UPSTREAM..HEAD")" ] || refuse "merge commits in origin/$BRANCH..HEAD; rebase them away first"
  dup="$(git rev-list --cherry-mark --right-only --no-merges "$UPSTREAM...HEAD" | sed -n 's/^=//p')"
  [ -z "$dup" ] || refuse "commit(s) already upstream by patch (maybe since reverted): $(echo $dup | cut -c1-80). Decide by hand: drop them or re-apply on purpose"

  PHASE=rebase
  if ! err="$(git rebase -q "$UPSTREAM" 2>&1)"; then
    files="$(git diff --name-only --diff-filter=U)"
    [ -e "$GITDIR/rebase-merge" ] && git rebase --abort >/dev/null 2>&1
    echo "safe-push: rebase onto origin/$BRANCH failed; nothing pushed." >&2
    if [ -n "$files" ]; then echo "conflicting files:" >&2; echo "$files" >&2; else echo "$err" >&2; fi
    exit 3
  fi
  PHASE=""
  n="$(git rev-list --count "$UPSTREAM..HEAD")"

  if [ -n "$TEST_CMD" ]; then
    head="$(git rev-parse HEAD)"
    bash -c "$TEST_CMD" || { echo "safe-push: test command failed; nothing pushed" >&2; exit 4; }
    [ "$(git rev-parse HEAD)" = "$head" ] && [ -z "$(git status --porcelain)" ] \
      || { echo "safe-push: test command changed tracked files or HEAD; nothing pushed" >&2; exit 4; }
  fi

  PHASE=push
  if out="$(git push -q origin -- "HEAD:refs/heads/$BRANCH" 2>&1)"; then
    PHASE=""
    echo "safe-push: pushed $n commit(s) to origin/$BRANCH ($(git rev-parse --short HEAD))"
    sync_shared
    exit 0
  fi
  PHASE=""
  if ! printf '%s' "$out" | grep -q -E 'fetch first|non-fast-forward|cannot lock ref|failed to update ref|stale info'; then
    echo "safe-push: push failed (not a race; not retrying):" >&2; echo "$out" >&2; exit 6
  fi
  if [ "$try" -ge "$TRIES" ]; then echo "safe-push: lost the race $TRIES times" >&2; exit 6; fi
  echo "safe-push: lost a race, retrying ($try/$TRIES)"
  sleep $((BACKOFF * try)); try=$((try + 1))
done
