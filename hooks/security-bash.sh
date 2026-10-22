#!/bin/bash
# PreToolUse hook for Bash (Claude Code and Codex): block destructive and secret-reading commands.
# Exit 0 = allow, Exit 2 = block (reason on stderr).
# The rules live in guard_bash.py (stdlib Python, T15): it parses the command instead of matching text, unwraps
# bash -c / eval / sudo / env / xargs / find -exec ... up to 10 levels, treats quoted text and heredoc bodies as
# data, and checks every reader of secret files. Codex may send the command as an argv list; it is handled too.
# A missing python3 or a crash blocks (fail closed).
HERE="$(cd "$(dirname "$0")" && pwd -P)"
python3 "$HERE/guard_bash.py" hook
rc=$?
if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
  echo "BLOCKED: the command guard could not run (exit $rc)." >&2
  exit 2
fi
exit "$rc"
