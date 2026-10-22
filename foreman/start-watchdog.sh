#!/usr/bin/env bash
# Start the factory watchdog once, and only the reviewed version: its sha256 must match the pin
# (the kit folder is protected from factory edits, so the factory can't swap in unreviewed daemon code).
# After verifying a new watchdog.py, update the pin:  shasum -a 256 watchdog/watchdog.py | cut -d' ' -f1 > watchdog/watchdog.sha256
# Usage: start-watchdog.sh DIR PIN_FILE   (starts DIR/watchdog/watchdog.py, logs to DIR/watchdog/watchdog.log)
set -u
DIR="$1"; PIN="$2"; WD="$DIR/watchdog/watchdog.py"
[ -f "$WD" ] || exit 0
pgrep -f "$WD" >/dev/null && exit 0
want="$(cat "$PIN" 2>/dev/null | tr -d '[:space:]')"
have="$(shasum -a 256 "$WD" | cut -d' ' -f1)"
if [ -z "$want" ] || [ "$want" != "$have" ]; then
  echo "watchdog not started: $WD sha256 $have does not match the pin in $PIN (review the change, then update the pin)" >&2
  exit 0
fi
nohup python3 "$WD" >> "$DIR/watchdog/watchdog.log" 2>&1 &
