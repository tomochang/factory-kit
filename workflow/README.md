# Factory workflow (T6)

The ten-step workflow every factory task follows: number → real-use research (imozuru) → KGI → spec and roadmap
→ suggest → refine → lock on go → REA on the best system → tests first → build. Full text: `WORKFLOW.md`.

- `WORKFLOW-remote.md` is the same workflow for a factory driven from the owner's phone (WhatsApp `F: go`,
  ask-me, safe-push, Codex builds through codex-run.sh). The kit's installer makes `{{WORK_DIR}}/CLAUDE.md` import it.
- `{{WORK_DIR}}/CLAUDE.md` imports a workflow file, so every Claude Code session started in `{{WORK_DIR}}` follows it.
  A running session picks up changes only after a restart.
- `{{WORK_DIR}}/TASKS.md` is the shared task-number list.
- `templates/` has KGI.md, SPEC.md, ROADMAP.md and REFERENCE.md to copy into a task repo.
- `check_task.py <repo>` checks a finished task repo: KGI fields filled, no template placeholders, REA
  evidence IDs (or "REA: not applicable - reason") in REFERENCE.md, spec locked in the first commit, tests
  committed before code. Exit 1 on any failure.

## Tests

    python3 -m unittest -v test_check_task

Tests build throwaway git repos in a temp directory only.
