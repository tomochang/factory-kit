# factory-kit

A software factory for Claude Code. Every task goes through the same ten steps:
number → real-use research (imozuru) → KGI → spec and roadmap → suggest → refine → lock on your go
→ REA on the best existing system → tests first → build. Full text: `workflow/WORKFLOW.md`.

## Install

Needs: Claude Code, git, Python 3.10+ (or `uv`), Node (for `npx rea-agents` in step 8).

    git clone <this repo> ~/factory-kit
    bash ~/factory-kit/install.sh

`install.sh` does this, and never overwrites a file that already exists:

- copies the `imozuru` research skill to `~/.claude/skills/imozuru`
- creates `~/factory/` with `CLAUDE.md` (imports the workflow), `TASKS.md` (task numbers) and a link
  `~/factory/workflow` → `~/factory-kit/workflow`

## Use

    cd ~/factory && claude

Then type the request, for example "build a CLI that counts words in a folder".
Claude numbers it (T1, T2, …), researches real uses, and shows you a short plan.
Reply `go T1` to let it build, or say what to change. Nothing is built before your go.

Research goes to `~/research/<topic>/`, each task's code to `~/factory/<repo>/`.

## Update

    cd ~/factory-kit && git pull

The workflow is a link, so the next Claude Code session in `~/factory` uses the new version.
Re-run `install.sh` only for a new skill version (delete `~/.claude/skills/imozuru` first).

## Tests

    cd workflow && python3 -m unittest -v test_check_task
