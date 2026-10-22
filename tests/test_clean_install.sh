#!/usr/bin/env bash
# KGI check: install the whole factory on a "clean Mac" from factory.env alone.
# A temporary HOME, a fresh copy of the kit, a fake Beeper Desktop API and stand-ins for claude/codex:
#   factory.env -> install.sh (print) -> install.sh --apply -> doctor.sh passes
#   -> the channel, started exactly as the generated mcp.json says, delivers an "F:" message the owner types
#      to the session (notifications/claude/channel) and the session's spec reply lands in the owner's own chat
#   -> foreman.sh starts claude with the channel -> the work folder files are rendered with WORK_DIR filled in
#   (default ~/factory: same text as before; re-install updates them) -> install.sh --uninstall takes the hooks and
#   the rendered workflow out again -> the same with a custom WORK_DIR: 0 hits for "~/factory" in what it renders.
# Never touches the real HOME, Beeper, agent configs or running processes. Exit 0 = PASS.
# What it cannot show locally: a real Claude Code session reading the event and writing the spec (KGI part
# measured after delivery by sending "F: ping" from the phone; see README "Check it works").
set -eu
SRC="$(cd "$(dirname "$0")/.." && pwd -P)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/factory-kit-clean.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
BEEPER_PID=""
cleanup() {
  [ -n "$BEEPER_PID" ] && kill "$BEEPER_PID" 2>/dev/null
  rm -r "$TMP" 2>/dev/null || true
}
trap cleanup EXIT
fail() { echo "clean-install: FAIL $*" >&2; exit 1; }
step() { echo "clean-install: $*"; }

for v in $(env | sed -n 's/^\(FACTORY_[A-Z_]*\|BEEPER_[A-Z_]*\|ASK_ME_[A-Z_]*\)=.*/\1/p'); do unset "$v"; done
unset OWNER_NAME CHAT_ID TOKEN_FILE BEEPER_BASE WORK_DIR PROTECTED_DIRS CLAUDE_CONFIG_DIR CODEX_HOME 2>/dev/null || true
export HOME="$TMP/home" SHELL=/bin/zsh TZ=UTC
mkdir -p "$HOME"

KIT="$TMP/factory-kit"
step "copy the kit as a clone has it"
python3 - "$SRC" "$KIT" <<'EOF'
import shutil, subprocess, sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
names = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=src, capture_output=True,
                       text=True, check=True).stdout.split("\0")
for n in filter(None, names):
    s = src / n
    if s.is_file():
        (dst / n).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, dst / n)
nm = src / "foreman/channel/node_modules"
if nm.is_dir():  # stay offline; install.sh runs npm ci when it is missing
    shutil.copytree(nm, dst / "foreman/channel/node_modules", symlinks=True)
EOF

step "start a fake Beeper"
python3 "$KIT/tests/fake_beeper.py" --port-file "$TMP/port" &
BEEPER_PID=$!
for _ in $(seq 50); do [ -s "$TMP/port" ] && break; sleep 0.1; done
[ -s "$TMP/port" ] || fail "fake Beeper did not start"
PORT="$(cat "$TMP/port")"

step "fill in factory.env (the only thing a new owner writes)"
mkdir -p "$HOME/.config/factory-kit"
printf 'fake-token-for-tests\n' > "$HOME/.config/factory-kit/beeper-token"
chmod 600 "$HOME/.config/factory-kit/beeper-token"
cat > "$KIT/factory.env" <<EOF
OWNER_NAME="Alex Example"
CHAT_ID="!self:beeper.local"
TOKEN_FILE="\$HOME/.config/factory-kit/beeper-token"
BEEPER_BASE="http://127.0.0.1:$PORT"
WORK_DIR="\$HOME/factory"
PROTECTED_DIRS="\$HOME/private-repo"
TZ="UTC"
ASK_ME_STATE="\$HOME/.factory-kit/ask-me"
EOF

mkdir -p "$TMP/bin"
cat > "$TMP/bin/claude" <<EOF
#!/bin/sh
if [ "\$1" = "--version" ]; then echo "2.1.307 (Claude Code)"; exit 0; fi
{ echo "claude cwd=\$(pwd -P) session=\${FACTORY_SESSION:-}"; for a in "\$@"; do echo "arg=\$a"; done; } >> "$TMP/bin/calls.log"
EOF
cat > "$TMP/bin/codex" <<'EOF'
#!/bin/sh
if [ "$1" = "--version" ]; then echo "codex-cli 0.163.0"; exit 0; fi
exit 0
EOF
chmod +x "$TMP/bin/claude" "$TMP/bin/codex"
export PATH="$TMP/bin:$PATH"

step "install.sh (print only)"
out="$(bash "$KIT/install.sh")" || fail "install.sh print mode"
echo "$out" | grep -q "would add to $HOME/.claude/settings.json" || fail "print mode did not show the settings lines"
[ ! -e "$HOME/.claude" ] || fail "print mode wrote into HOME"

step "install.sh --apply"
bash "$KIT/install.sh" --apply > "$TMP/apply.log" 2>&1 || { cat "$TMP/apply.log"; fail "install.sh --apply"; }
for s in imozuru foreman ask-me safe-push; do
  [ -L "$HOME/.claude/skills/$s" ] && [ -L "$HOME/.agents/skills/$s" ] || fail "skill $s not linked for both tools"
done

step "doctor.sh"
bash "$KIT/doctor.sh" > "$TMP/doctor.log" 2>&1 || { cat "$TMP/doctor.log"; fail "doctor.sh"; }
grep -q "PASS own chat" "$TMP/doctor.log" || fail "doctor did not check the own chat"

step "F: message through the channel's fake transport"
node "$KIT/foreman/channel/test/e2e-installed.mjs" "$KIT/generated/mcp.json" "http://127.0.0.1:$PORT" \
  || fail "F: task did not reach the session"

step "foreman.sh starts claude with the channel"
FACTORY_WATCHDOG=off bash "$KIT/foreman/foreman.sh" || fail "foreman.sh"
grep -qx "arg=server:whatsapp" "$TMP/bin/calls.log" || fail "foreman did not load the whatsapp channel"
grep -qx "arg=$KIT/generated/mcp.json" "$TMP/bin/calls.log" || fail "foreman did not use the generated mcp.json"

# The work folder files are rendered from the kit (workflow/*.md, templates/factory/*) with WORK_DIR filled in.
RENDERED="workflow/WORKFLOW.md workflow/WORKFLOW-remote.md workflow/README.md CLAUDE.md AGENTS.md TASKS.md"
src_of() { case "$1" in workflow/*) echo "$KIT/$1" ;; *) echo "$KIT/templates/factory/$1" ;; esac; }
check_rendered() {  # check_rendered <work dir> <how the files must name it>
  [ -d "$1/workflow" ] && [ ! -L "$1/workflow" ] || fail "$1/workflow is not a rendered folder"
  for f in $RENDERED; do
    [ -f "$1/$f" ] || fail "$1/$f not rendered"
    ! grep -q '{{' "$1/$f" || fail "placeholder left in $1/$f"
    sed "s#{{WORK_DIR}}#$2#g" "$(src_of "$f")" | cmp -s - "$1/$f" || fail "$1/$f differs from its template"
  done
  for f in check_task.py templates/KGI.md templates/SPEC.md templates/ROADMAP.md templates/REFERENCE.md; do
    [ -f "$1/workflow/$f" ] || fail "$1/workflow/$f missing"
  done
  grep -qF "Read \`$2/TASKS.md\`" "$1/workflow/WORKFLOW.md" || fail "WORKFLOW.md does not name $2"
  grep -qF "$2/workflow/check_task.py $2/<repo>" "$1/workflow/WORKFLOW-remote.md" || fail "WORKFLOW-remote.md does not name $2"
}

step "default WORK_DIR: the rendered workflow is the same text as before (~/factory)"
check_rendered "$HOME/factory" "~/factory"
for f in workflow/WORKFLOW.md workflow/WORKFLOW-remote.md workflow/README.md; do
  grep -q "~/factory/TASKS.md" "$HOME/factory/$f" || fail "default $f does not name ~/factory/TASKS.md"
done

step "re-running install updates the rendered files"
echo "Added in a kit update." >> "$KIT/workflow/WORKFLOW.md"
bash "$KIT/install.sh" --apply > "$TMP/apply2.log" 2>&1 || { cat "$TMP/apply2.log"; fail "second --apply"; }
tail -1 "$HOME/factory/workflow/WORKFLOW.md" | grep -qx "Added in a kit update." || fail "re-install did not update"
echo "| T1 | my task | done | 2026-10-22 |" >> "$HOME/factory/TASKS.md"

step "install.sh --uninstall"
bash "$KIT/install.sh" --uninstall > "$TMP/uninstall.log" 2>&1 || { cat "$TMP/uninstall.log"; fail "uninstall"; }
if grep -q "$KIT" "$HOME/.claude/settings.json" "$HOME/.codex/hooks.json" 2>/dev/null; then fail "hooks left after uninstall"; fi
[ ! -e "$HOME/.claude/skills/foreman" ] || fail "skill link left after uninstall"
[ ! -e "$HOME/factory/workflow" ] || fail "rendered workflow left after uninstall"
grep -q "my task" "$HOME/factory/TASKS.md" || fail "uninstall removed the owner's TASKS.md"

step "custom WORK_DIR: install into a work folder that is not ~/factory"
sed -i.bak 's#^WORK_DIR=.*#WORK_DIR="$HOME/projects/shop"#' "$KIT/factory.env" && rm "$KIT/factory.env.bak"
bash "$KIT/install.sh" --apply > "$TMP/apply3.log" 2>&1 || { cat "$TMP/apply3.log"; fail "custom --apply"; }
WORK="$HOME/projects/shop"
check_rendered "$WORK" "~/projects/shop"
hits="$(grep -rF "~/factory" "$WORK" "$KIT/generated" | grep -c . || true)"
[ "$hits" = 0 ] || { grep -rnF "~/factory" "$WORK" "$KIT/generated"; fail "$hits hits for ~/factory with a custom WORK_DIR"; }
grep -qF "$WORK/TASKS.md" "$KIT/generated/channel-instructions.txt" || fail "channel instructions do not name $WORK"
bash "$KIT/doctor.sh" > "$TMP/doctor2.log" 2>&1 || { cat "$TMP/doctor2.log"; fail "doctor.sh with a custom WORK_DIR"; }
bash "$KIT/install.sh" --uninstall > "$TMP/uninstall2.log" 2>&1 || { cat "$TMP/uninstall2.log"; fail "uninstall"; }
[ ! -e "$WORK/workflow" ] || fail "rendered workflow left after uninstall (custom WORK_DIR)"
[ -f "$WORK/TASKS.md" ] || fail "uninstall removed TASKS.md (custom WORK_DIR)"

echo "clean-install: PASS"
