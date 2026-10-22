# Factory

Every factory task follows `workflow/WORKFLOW-remote.md` in this folder: read it first and follow its steps.
Task numbers live in `TASKS.md`. The skills `ask-me`, `safe-push` and `foreman` (in ~/.agents/skills) explain the
tools. Codex cannot run commands in the background: ask with `ask-me ... --timeout 5` (exit 3 is expected) and
check later with `--wait <code> --timeout 5`.
