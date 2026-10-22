#!/usr/bin/env bash
# Check that this Mac's factory works: settings, Beeper and the own chat, hooks (and their Codex trust),
# Claude Code / Codex versions, the dev-channel setup and the watchdog. Sends nothing.
# Prints PASS / WARN / FAIL per check; exit 1 when anything FAILs.
set -u
KIT="$(cd "$(dirname "$0")" && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
KITENV="python3 $KIT/lib/kitenv.py"
fails=0
pass() { echo "PASS $*"; }
warn() { echo "WARN $*"; }
fail() { echo "FAIL $*"; fails=$((fails + 1)); }

# settings
if msg="$($KITENV check 2>&1)"; then
  pass "factory.env: $($KITENV get ENV_FILE)"
else
  fail "factory.env: $msg"
  echo "doctor: fix factory.env first"
  exit 1
fi
TOKEN="$($KITENV get TOKEN_FILE)"
if [ ! -f "$TOKEN" ]; then
  fail "token file: $TOKEN is missing (Beeper Desktop > Settings > Developers > API token)"
elif [ "$(stat -f '%Lp' "$TOKEN" 2>/dev/null || stat -c '%a' "$TOKEN")" != 600 ]; then
  warn "token file: $TOKEN can be read by other users: chmod 600 $TOKEN"
else
  pass "token file: $TOKEN (mode 600)"
fi

# Beeper and the own-chat check (ask-me.py --check sends nothing)
out="$(python3 "$KIT/scripts/ask-me.py" --check 2>&1)"
case $? in
  0) pass "Beeper reachable: $($KITENV get BEEPER_BASE)"; pass "own chat: the configured chat is your own 1:1 chat" ;;
  2) pass "Beeper reachable: $($KITENV get BEEPER_BASE)"; fail "own chat: ${out#ask-me: }" ;;
  *) fail "Beeper reachable: ${out#ask-me: } (is Beeper Desktop running with its API on?)"
     fail "own chat: not checked (Beeper unreachable)" ;;
esac

# tools
if v="$(claude --version 2>/dev/null)" && [ -n "$v" ]; then
  pass "Claude Code ${v%% (*}"
else
  fail "Claude Code: \`claude --version\` failed (install Claude Code)"
fi
if v="$(command codex --version 2>/dev/null)" && [ -n "$v" ]; then
  pass "Codex $v"
else
  warn "Codex: not found (only needed for Codex builds through codex-run.sh)"
fi
for t in node npm jq git; do
  command -v "$t" >/dev/null 2>&1 || fail "$t: not found"
done

# hooks
python3 - "$KIT" "$HOME" <<'PY'
import json, os, re, sys
kit, home = sys.argv[1:]

def load(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None

def kit_cmds(data, event=None):
    return [h.get("command", "") for e, groups in ((data or {}).get("hooks") or {}).items() if event in (None, e)
            for g in groups for h in g.get("hooks", []) if kit in h.get("command", "")]

claude = load(os.path.join(home, ".claude", "settings.json"))
ws = os.path.join(kit, "generated", "workspace-settings.json")
if any("ask_me_stop.py" in c for c in kit_cmds(claude, "Stop")) and os.path.isfile(ws):
    print("PASS Claude hooks: Stop hook in ~/.claude/settings.json; guards in generated/workspace-settings.json"
          " (foreman.sh installs them in the work folder)")
else:
    print(f"FAIL Claude hooks: not installed; run: bash {kit}/install.sh --apply")
codex_file = os.path.join(home, ".codex", "hooks.json")
codex = load(codex_file)
cmds = kit_cmds(codex)
if any("factory_guard.py" in c for c in cmds) and any("ask_me_stop.py" in c for c in cmds):
    print("PASS Codex hooks: guards (with apply_patch) and Stop hook in ~/.codex/hooks.json")
    # Codex keeps trust in config.toml as [hooks.state."<file>:<event>:<group>:<handler>"] trusted_hash = ...
    try:
        toml = open(os.path.join(home, ".codex", "config.toml")).read()
    except OSError:
        toml = ""
    snake = lambda e: re.sub(r"(?<!^)(?=[A-Z])", "_", e).lower()
    missing = 0
    for event, groups in codex["hooks"].items():
        for gi, g in enumerate(groups):
            for hi, h in enumerate(g.get("hooks", [])):
                if kit not in h.get("command", ""):
                    continue
                key = re.escape(f'[hooks.state."{codex_file}:{snake(event)}:{gi}:{hi}"]')
                if not re.search(key + r"\s*\n(?:[^\[]*\n)*?\s*trusted_hash\s*=", toml):
                    missing += 1
    if missing:
        print(f"WARN Codex hook trust: {missing} factory-kit hook(s) not trusted yet; start codex, type /hooks"
              " and trust them (Codex skips untrusted hooks)")
    else:
        print("PASS Codex hook trust: every factory-kit hook has a trust record")
else:
    print(f"FAIL Codex hooks: not installed; run: bash {kit}/install.sh --apply")
PY
hook_out=$?
[ "$hook_out" = 0 ] || fail "hook check could not run"

# dev channel: the generated MCP config and the flag foreman.sh passes
MCP="$KIT/generated/mcp.json"
if [ -f "$MCP" ] && python3 -c 'import json,sys; s=json.load(open(sys.argv[1]))["mcpServers"]["whatsapp"]; import os; sys.exit(0 if os.path.isfile(s["args"][0]) else 1)' "$MCP" 2>/dev/null \
   && grep -q -- "--dangerously-load-development-channels server:whatsapp" "$KIT/foreman/foreman.sh"; then
  pass "dev channel: server:whatsapp in $MCP, loaded with --dangerously-load-development-channels (answer the one-time dialog with \"local development\")"
else
  fail "dev channel: $MCP missing or broken; run: bash $KIT/install.sh --apply"
fi
[ -d "$KIT/foreman/channel/node_modules" ] || fail "channel modules: run: cd $KIT/foreman/channel && npm ci"

# watchdog
WD="$KIT/watchdog/watchdog.py"
if pgrep -f "$WD" >/dev/null 2>&1; then
  pass "watchdog running"
else
  warn "watchdog not running (foreman.sh starts it; it alerts on stop, restart and a quiet channel)"
fi
want="$(tr -d '[:space:]' < "$KIT/watchdog/watchdog.sha256" 2>/dev/null)"
have="$(shasum -a 256 "$WD" | cut -d' ' -f1)"
[ "$want" = "$have" ] || fail "watchdog pin: watchdog.py does not match watchdog/watchdog.sha256 (foreman.sh will not start it)"

if [ "$fails" -gt 0 ]; then echo "doctor: $fails check(s) failed"; exit 1; fi
echo "doctor: all required checks passed"
