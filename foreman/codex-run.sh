#!/usr/bin/env bash
# Start a factory build in Codex: `command codex exec --cd <worktree> -` with the task file on stdin.
# CODEX_SANDBOX=1 in factory.env (off by default) adds `--sandbox workspace-write`: commands may write only the
# worktree, but the network is off and the worktree's git directory is read-only, so git commit and ask-me don't
# work inside the build (commit after Codex finishes). The Bash hooks apply either way.
# `command` skips any `codex` shell function (one that forces --cd elsewhere would run the build in the wrong
# folder). FACTORY_SESSION=1 makes the kit's guards (in ~/.codex/hooks.json) treat it as a factory session.
# Codex has no channel push: the Claude foreman stays the inbox and starts this in the background.
# Codex can't run commands in the background either, so tell it to ask with
#   ask-me.py --from <task> --timeout 5 "<question>"   (exit 3 expected) and check later with --wait <code> --timeout 5
# T18: the build runs with --json; its events go to <task>.codex.jsonl and the last message to <task>.codex-last.md,
# both beside the task file (mode 600: they hold the whole run), and the last message is printed when it ends.
# Codex's exit code is kept and is the verdict (codex exec exits 1 on a non-retried error or a failed/interrupted
# turn, exec lib.rs:1343-1359); exit != 0 sends "Codex build failed: <task> (exit N[, turn.failed|error])" to the
# owner's own chat (ask-me --tell). That a build finished, stopped silently or sits idle comes from the watchdog's Codex watch.
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
SANDBOX=()
[ "$(python3 "$KIT/lib/kitenv.py" get CODEX_SANDBOX)" = "1" ] && SANDBOX=(--sandbox workspace-write)
TASK_ABS="$(cd "$(dirname "$TASK")" && pwd -P)/$(basename "$TASK")"
NAME="$(basename "$TASK_ABS")"; NAME="${NAME%.*}"; [ -n "$NAME" ] || NAME="$(basename "$TASK_ABS")"
BASE="$(dirname "$TASK_ABS")/$NAME"
EVENTS="$BASE.codex.jsonl"; LAST="$BASE.codex-last.md"
rm -f "$LAST"
set +e
umask 077
command codex exec --json -o "$LAST" --cd "$WT" ${SANDBOX[@]+"${SANDBOX[@]}"} "$@" - < "$TASK" > "$EVENTS"
rc=$?
set -e
# the first failure event's type only (turn.failed or error): never its text, which can hold anything
failed="$(python3 - "$EVENTS" <<'PY'
import json, sys
for line in open(sys.argv[1], errors="replace"):
    try:
        t = json.loads(line).get("type")
    except (ValueError, AttributeError):
        continue
    if t in ("turn.failed", "error"):
        print(t)
        break
PY
)" || failed=""
[ -f "$LAST" ] && cat "$LAST"
echo "codex-run: codex exit $rc; events in $EVENTS" >&2
if [ "$rc" -ne 0 ]; then
  msg="Codex build failed: $NAME (exit $rc${failed:+, $failed})"
  python3 "$KIT/scripts/ask-me.py" --from codex-run --tell "$msg" >/dev/null 2>&1 \
    || echo "codex-run: could not send \"$msg\" (is Beeper Desktop running?)" >&2
  exit "$rc"
fi
exit 0
