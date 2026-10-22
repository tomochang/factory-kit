#!/usr/bin/env bash
# Install factory-kit on this Mac from factory.env alone.
#
#   install.sh              dry run: print every change, including the exact lines for the global files
#   install.sh --apply      write them (a backup is taken of every global file before it changes)
#   install.sh --uninstall  take out what the kit added (hooks, skill links, shell lines, generated/, the
#                           rendered <WORK_DIR>/workflow); TASKS.md and the task repos are kept
#
# What --apply does:
#   - generated/: mcp.json, workspace-settings.json, claude-settings.json, codex-hooks.json,
#     channel-instructions.txt, with absolute paths (lib/render.py)
#   - ~/.claude/settings.json: the Stop hook (ask_me_stop.py). The guards are workspace-scoped for Claude Code:
#     foreman.sh copies generated/workspace-settings.json to <WORK_DIR>/.claude/settings.json.
#   - ~/.codex/hooks.json: the same guards (security-bash, security-file-write, factory_guard, matching
#     apply_patch) and the Stop hook. Codex runs new hooks only after you trust them with /hooks.
#   - shell rc: FACTORY_KIT and a `foreman` alias
#   - each kit skill linked into ~/.claude/skills and ~/.agents/skills (names that exist are skipped)
#   - <WORK_DIR>/workflow: the kit's workflow files rendered with WORK_DIR filled in (rewritten on each apply)
#   - <WORK_DIR>: CLAUDE.md, AGENTS.md, TASKS.md (existing files are kept)
#   - the channel's node modules (npm ci) when missing
set -eu
KIT="$(cd "$(dirname "$0")" && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1  # no .pyc files in the kit or caches in HOME from a dry run
MODE=print
case "${1:-}" in
  "") ;;
  --apply) MODE=apply ;;
  --uninstall) MODE=uninstall ;;
  -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "install.sh: unknown argument $1 (use --apply or --uninstall)" >&2; exit 2 ;;
esac

KITENV="python3 $KIT/lib/kitenv.py"
HOOKFILE="python3 $KIT/lib/hookfile.py"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CODEX_HOOKS="$HOME/.codex/hooks.json"
SKILL_BASES="$HOME/.claude/skills $HOME/.agents/skills"
SKILLS="$(cd "$KIT/skills" && for d in */; do [ -f "$d/SKILL.md" ] && printf '%s ' "${d%/}"; done)"
case "$(basename "${SHELL:-/bin/zsh}")" in
  zsh) RC="$HOME/.zshrc" ;;
  bash) RC="$HOME/.bashrc" ;;
  *) RC="$HOME/.profile" ;;
esac
BEGIN="# >>> factory-kit >>>"
END="# <<< factory-kit <<<"
rc_block() {
  printf '%s\n%s\n%s\n%s\n' "$BEGIN" "export FACTORY_KIT=\"$KIT\"" \
    "alias foreman='$KIT/foreman/foreman.sh'" "$END"
}
backup() {
  if [ -e "$1" ]; then
    b="$1.factory-kit-backup-$(date +%Y%m%d%H%M%S)"
    cp -p "$1" "$b" && echo "backup: $b"
  fi
}
trust_step() {
  cat <<EOF

Codex hook trust (once per Mac, and again after a hook changes):
  start codex, type /hooks, review the factory-kit hooks in $CODEX_HOOKS and trust them.
  Codex skips new or changed hooks until they are trusted; doctor.sh shows whether they are.
EOF
}

if [ "$MODE" = uninstall ]; then
  echo "factory-kit uninstall ($KIT)"
  for f in "$CLAUDE_SETTINGS" "$CODEX_HOOKS"; do
    if [ -f "$f" ]; then echo "$f: $($HOOKFILE remove "$f" "$KIT" | tail -1)"; fi
  done
  for base in $SKILL_BASES; do
    for s in $SKILLS; do
      link="$base/$s"
      if [ -L "$link" ] && [ "$(cd "$link" 2>/dev/null && pwd -P)" = "$(cd "$KIT/skills/$s" && pwd -P)" ]; then
        rm "$link" && echo "unlinked: $link"
      fi
    done
  done
  if [ -f "$RC" ] && grep -qF "$BEGIN" "$RC"; then
    backup "$RC"
    python3 - "$RC" "$BEGIN" "$END" <<'PY'
import sys
path, begin, end = sys.argv[1:]
out, skip = [], False
for line in open(path).read().splitlines(True):
    if line.rstrip("\n") == begin:
        skip = True
        continue
    if skip and line.rstrip("\n") == end:
        skip = False
        continue
    if not skip:
        out.append(line)
open(path, "w").write("".join(out))
PY
    echo "removed the factory-kit lines from $RC"
  fi
  python3 "$KIT/lib/render.py" --work uninstall
  if [ -d "$KIT/generated" ]; then
    for f in "$KIT"/generated/*; do if [ -e "$f" ]; then rm "$f"; fi; done
    rmdir "$KIT/generated" && echo "removed: $KIT/generated"
  fi
  echo "kept: the work folder's TASKS.md and task repos (delete them yourself if you want)"
  exit 0
fi

$KITENV check || exit 2
WORK="$($KITENV get WORK_DIR)"
TOKEN="$($KITENV get TOKEN_FILE)"

if [ "$MODE" = print ]; then
  echo "factory-kit install, dry run: nothing is written. Run again with --apply to make these changes."
  GEN="$(mktemp -d "${TMPDIR:-/tmp}/factory-kit-preview.XXXXXX")"
  echo "would write $KIT/generated/ (mcp.json, workspace-settings.json, claude-settings.json, codex-hooks.json, channel-instructions.txt)"
else
  echo "factory-kit install --apply ($KIT)"
  GEN="$KIT/generated"
fi
python3 "$KIT/lib/render.py" --out "$GEN" >/dev/null
if [ "$MODE" = apply ]; then echo "wrote $GEN/"; fi

# --- global files
for pair in "$CLAUDE_SETTINGS:claude-settings.json" "$CODEX_HOOKS:codex-hooks.json"; do
  target="${pair%%:*}"; frag="$GEN/${pair##*:}"
  if [ "$MODE" = print ]; then
    echo
    echo "would add to $target (a backup is taken first):"
    $HOOKFILE show "$frag"
  else
    echo "$target: $($HOOKFILE add "$target" "$frag" | tr '\n' ' ')"
  fi
done
if [ "$MODE" = print ]; then
  echo
  echo "would add to $RC:"
  rc_block
elif [ -f "$RC" ] && grep -qF "$BEGIN" "$RC"; then
  echo "$RC: unchanged"
else
  backup "$RC"
  { if [ -s "$RC" ] && [ -n "$(tail -c1 "$RC")" ]; then echo; fi; rc_block; } >> "$RC"
  echo "$RC: changed"
fi

# --- skills for both tools
echo
for base in $SKILL_BASES; do
  for s in $SKILLS; do
    link="$base/$s"
    if [ -e "$link" ] || [ -L "$link" ]; then
      echo "skip: $link exists"
    elif [ "$MODE" = print ]; then
      echo "would link $link -> $KIT/skills/$s"
    else
      mkdir -p "$base" && ln -s "$KIT/skills/$s" "$link" && echo "linked: $link"
    fi
  done
done

# --- work folder: workflow/ rendered with WORK_DIR filled in (updated on every apply), owner files when missing
python3 "$KIT/lib/render.py" --work "$MODE"

# --- channel modules
if [ ! -d "$KIT/foreman/channel/node_modules" ]; then
  if [ "$MODE" = print ]; then
    echo "would run npm ci in $KIT/foreman/channel"
  else
    (cd "$KIT/foreman/channel" && npm ci --no-audit --no-fund)
  fi
fi

# --- warnings
if [ ! -f "$TOKEN" ]; then
  echo "warning: the Beeper token file $TOKEN does not exist yet (Beeper Desktop > Settings > Developers > API token)"
elif [ "$(stat -f '%Lp' "$TOKEN" 2>/dev/null || stat -c '%a' "$TOKEN")" != 600 ]; then
  echo "warning: $TOKEN can be read by other users: chmod 600 $TOKEN"
fi
if [ -f "$RC" ] && grep -qE '^[[:space:]]*(function[[:space:]]+)?codex[[:space:]]*\(\)' "$RC"; then
  echo "warning: $RC defines a codex() shell function. Factory builds bypass it (codex-run.sh calls \`command codex\`);"
  echo "         if you start Codex for a factory task by hand, use \`command codex --cd <worktree>\` too."
fi

if [ "$MODE" = print ]; then rm -r "$GEN"; fi
trust_step
if [ "$MODE" = apply ]; then
  echo
  echo "Next: bash $KIT/doctor.sh, then start the foreman: $KIT/foreman/foreman.sh"
fi
