#!/usr/bin/env bash
# Start a factory build in Claude Code inside the OS sandbox (T15 R5):
#   command claude -p --settings <kit>/generated/build-settings.json --permission-mode acceptEdits  (cwd: worktree)
# with the task file on stdin. build-settings.json turns the sandbox on: Bash may write only the worktree (the
# working directory) plus npm/pip/uv caches, can't read ~/.ssh, ~/.aws, ~/.config/gh, ~/.gnupg, the Beeper token
# file or factory.env, and can't retry outside the sandbox. git, safe-push and ask-me run outside it (a worktree's
# .git lives in the main repo, issue #46000) and stay covered by the Bash hooks. Passing the sandbox with
# --settings makes it admin-required, so settings files in the repo can't loosen it.
# The interactive foreman is not sandboxed; only builds started here are.
# Note: `claude -p` is headless use of Claude Code (it may count as programmatic usage on your plan).
# `command` skips any `claude` shell function. FACTORY_SESSION=1 marks the run for factory_guard.py.
# usage: claude-run.sh <worktree> <task-file> [extra claude arguments]
set -eu
KIT="$(cd "$(dirname "$0")/.." && pwd -P)"
SETTINGS="$KIT/generated/build-settings.json"
refuse() { echo "claude-run: $1" >&2; exit 2; }
[ $# -ge 2 ] || refuse "usage: claude-run.sh <worktree> <task-file> [claude args]"
[ -d "$1" ] || refuse "no such worktree: $1"
[ -f "$2" ] || refuse "no such task file: $2"
[ -f "$SETTINGS" ] || refuse "$SETTINGS is missing; run: bash $KIT/install.sh --apply"
WT="$(cd "$1" && pwd -P)"; TASK="$2"; shift 2
KIT_LIB="$KIT/lib" python3 - "$WT" <<'PY' || refuse "refusing to build in a protected folder: $WT"
import os, sys
sys.path.insert(0, os.path.join(os.environ["KIT_LIB"]))
import kitenv
wt = os.path.realpath(sys.argv[1])
for d in kitenv.load()["PROTECTED_DIRS"]:
    d = os.path.realpath(d)
    if wt == d or wt.startswith(d + os.sep):
        sys.exit(1)
PY
export FACTORY_SESSION=1 FACTORY_ENV="${FACTORY_ENV:-$KIT/factory.env}"
cd "$WT"
command claude -p --settings "$SETTINGS" --permission-mode acceptEdits "$@" < "$TASK"
