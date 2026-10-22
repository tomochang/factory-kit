#!/usr/bin/env bash
# Start the factory watchdog once, and only the reviewed version: its sha256 must match the pin
# (the kit folder is protected from factory edits, so the factory can't swap in unreviewed daemon code).
# After verifying a new watchdog.py, update the pin:  shasum -a 256 watchdog/watchdog.py | cut -d' ' -f1 > watchdog/watchdog.sha256
# Usage: start-watchdog.sh DIR PIN_FILE   (starts DIR/watchdog/watchdog.py, logs to DIR/watchdog/watchdog.log)
set -u
DIR="$1"; PIN="$2"; WD="$DIR/watchdog/watchdog.py"
[ -f "$WD" ] || exit 0
want="$(cat "$PIN" 2>/dev/null | tr -d '[:space:]')"
have="$(shasum -a 256 "$WD" | cut -d' ' -f1)"
RUN="$DIR/watchdog/watchdog.running"   # sha256 of the version started last
if pgrep -f "$WD" >/dev/null; then
  # a running watchdog keeps the code it started with: name an older one, with the command to restart it
  if [ -n "$want" ] && [ "$want" = "$have" ] && [ "$(cat "$RUN" 2>/dev/null)" != "$have" ]; then
    echo "watchdog: an older version is still running; restart it to load the reviewed $WD:" >&2
    echo "  pkill -f $WD; sleep 1; bash $0 $DIR $PIN" >&2
  fi
  exit 0
fi
if [ -z "$want" ] || [ "$want" != "$have" ]; then
  echo "watchdog not started: $WD sha256 $have does not match the pin in $PIN (review the change, then update the pin)" >&2
  exit 0
fi
nohup python3 "$WD" >> "$DIR/watchdog/watchdog.log" 2>&1 &
echo "$have" > "$RUN"
