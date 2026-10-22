#!/usr/bin/env bash
# Start the factory foreman: one Claude Code session whose inbox is the owner's own WhatsApp chat.
# It runs in WORK_DIR (from factory.env; never inside a protected folder), with the kit's command/file guards
# installed as WORK_DIR/.claude/settings.json. Run install.sh --apply first.
# Keep this terminal open and the Mac awake (caffeinate can't stop lid-close sleep). Ctrl-C stops it.
# The first start shows a one-time warning for development channels: choose "I am using this for local development".
# FACTORY_WATCHDOG=off skips starting the watchdog. Extra arguments go to claude.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd -P)"
KIT="$(cd "$HERE/.." && pwd -P)"
GEN="$KIT/generated"
if [ ! -f "$GEN/mcp.json" ] || [ ! -f "$GEN/workspace-settings.json" ]; then
  echo "foreman: $GEN is missing; run: bash $KIT/install.sh --apply" >&2
  exit 2
fi
python3 "$KIT/lib/kitenv.py" check || exit 2
WORK="$(python3 "$KIT/lib/kitenv.py" get WORK_DIR)"
[ -d "$HERE/channel/node_modules" ] || (cd "$HERE/channel" && npm ci --no-audit --no-fund)
mkdir -p "$WORK/.claude"
cp "$GEN/workspace-settings.json" "$WORK/.claude/settings.json"
cd "$WORK"
export FACTORY_SESSION=1 FACTORY_ENV="${FACTORY_ENV:-$KIT/factory.env}"
# Watchdog: one message to the owner's own chat when the foreman stops, comes back or its channel goes quiet.
# Started only once, and only the version pinned in watchdog/watchdog.sha256; it outlives this session.
if [ "${FACTORY_WATCHDOG:-on}" != "off" ]; then
  bash "$HERE/start-watchdog.sh" "$KIT" "$KIT/watchdog/watchdog.sha256"
fi
KEEP_AWAKE=""
command -v caffeinate >/dev/null 2>&1 && KEEP_AWAKE="caffeinate -i"
exec $KEEP_AWAKE claude --mcp-config "$GEN/mcp.json" \
  --dangerously-load-development-channels server:whatsapp "$@"
