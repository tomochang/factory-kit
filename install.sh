#!/usr/bin/env bash
# Install factory-kit: the imozuru skill and a ~/factory folder that follows workflow/WORKFLOW.md.
# Never overwrites an existing file.
set -eu
KIT="$(cd "$(dirname "$0")" && pwd)"
SKILLS="$HOME/.claude/skills"
WORK="${FACTORY_DIR:-$HOME/factory}"

mkdir -p "$SKILLS" "$WORK"
if [ -e "$SKILLS/imozuru" ]; then
  echo "skip: $SKILLS/imozuru exists"
else
  cp -R "$KIT/skills/imozuru" "$SKILLS/imozuru" && echo "installed: $SKILLS/imozuru"
fi
if [ -e "$WORK/workflow" ]; then
  echo "skip: $WORK/workflow exists"
else
  ln -s "$KIT/workflow" "$WORK/workflow" && echo "linked: $WORK/workflow"
fi
if [ -e "$WORK/CLAUDE.md" ]; then
  echo "skip: $WORK/CLAUDE.md exists"
else
  printf '%s\n\n%s\n\n%s\n' "# Factory" \
    'Every factory task follows the workflow below. Task numbers live in `TASKS.md`.' \
    '@workflow/WORKFLOW.md' > "$WORK/CLAUDE.md" && echo "created: $WORK/CLAUDE.md"
fi
if [ -e "$WORK/TASKS.md" ]; then
  echo "skip: $WORK/TASKS.md exists"
else
  printf '%s\n\n%s\n\n%s\n%s\n' "# Factory tasks" \
    'Shared by every factory session. Take the next number before doing anything else (workflow step 1).' \
    '| T | Title | State | Date |' '|---|-------|-------|------|' > "$WORK/TASKS.md" && echo "created: $WORK/TASKS.md"
fi
echo "done. Start with: cd $WORK && claude"
