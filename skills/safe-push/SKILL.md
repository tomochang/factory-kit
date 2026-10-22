---
name: safe-push
description: Push the commits of an agent's own git worktree to origin safely - only from a linked worktree, only when it is clean and has no merge commits, after a rebase onto origin and an optional test command, retrying only after a lost race. Use instead of git push when a factory task is done. Works from Claude Code and Codex.
---

# safe-push: push from your own worktree

The script is `scripts/safe-push` in this skill's folder (it runs the kit's `scripts/safe-push.sh`;
`$FACTORY_KIT/scripts/safe-push.sh` is the same file).

    scripts/safe-push --branch main --test "<the full test command>"

Run it from inside the task's linked worktree (for example `git worktree add -b t7 /private/tmp/t7-wt origin/main`),
never from the shared checkout: it refuses there. Commit everything first; it refuses with uncommitted or
untracked files, a rebase or merge in progress, or merge commits. It rebases onto `origin/<branch>`, runs the test
command (nothing is pushed if it fails or changes tracked files), pushes, and retries only when another writer won
a race. Afterwards it fast-forwards the shared checkout when that is on the same branch and clean.

Exit codes: 0 pushed (or nothing to push), 2 refused, 3 rebase conflict (nothing pushed; the conflicting files are
listed), 4 test failed, 6 push failed, 130 interrupted.

Push only to repos the owner owns. Pushing to someone else's repo needs the owner's approval at the Mac.
