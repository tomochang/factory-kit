#!/usr/bin/env bash
# Start a factory build in Codex: `command codex exec --cd <worktree> -` with the task file on stdin.
# `command` skips any `codex` shell function (one that forces --cd elsewhere would run the build in the wrong
# folder). FACTORY_SESSION=1 makes the kit's guards (in ~/.codex/hooks.json) treat it as a factory session.
# Codex has no channel push: the Claude foreman stays the inbox and starts this in the background.
# Codex can't run commands in the background either, so tell it to ask with
#   ask-me.py --from <task> --timeout 5 "<question>"   (exit 3 expected) and check later with --wait <code> --timeout 5
# usage: codex-run.sh <worktree> <task-file> [extra codex exec arguments]
set -eu
KIT="$(cd "$(dirname "$0")/.." && pwd -P)"
refuse() { echo "codex-run: $1" >&2; exit 2; }
[ $# -ge 2 ] || refuse "usage: codex-run.sh <worktree> <task-file> [codex exec args]"
[ -d "$1" ] || refuse "no such worktree: $1"
[ -f "$2" ] || refuse "no such task file: $2"
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
command codex exec --cd "$WT" "$@" - < "$TASK"
