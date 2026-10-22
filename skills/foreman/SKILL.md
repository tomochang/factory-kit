---
name: foreman
description: Run or work inside the factory foreman - the Claude Code session whose inbox is the owner's own WhatsApp chat (F: messages), which numbers tasks, sends specs, starts background builds in worktrees (Claude Code or Codex) after the owner's F: go, and reports back. Use when starting or checking the foreman, handing a build to Codex, or following the remote factory workflow.
---

# foreman: the factory's remote inbox

The kit (`$FACTORY_KIT`) holds everything; `factory.env` holds the owner's values.

## Start and check

    $FACTORY_KIT/foreman/foreman.sh      # Claude Code with the whatsapp channel, in WORK_DIR; keep the terminal open
    bash $FACTORY_KIT/doctor.sh          # Beeper, own chat, hooks and their Codex trust, versions, watchdog

The first start shows a one-time dialog for development channels: choose "I am using this for local development".
The watchdog (started by foreman.sh, pinned by `watchdog/watchdog.sha256`) messages the owner when the foreman
stops, restarts, or its channel stops reading the chat.

## The flow

Follow `workflow/WORKFLOW-remote.md` in the work folder. In short: a new `F:` task gets the next number from
`TASKS.md` and a short spec reply ending "Reply F: go T<n>"; nothing is built before the owner's own `F: go T<n>`.
Then start a background agent in its own git worktree under /private/tmp; it writes tests first, builds, and pushes
with the safe-push skill if the repo has a remote. Reply with what changed, or the question it needs answered.

## Builds in Codex

Codex has no channel push, so the Claude foreman stays the inbox. To build in Codex, write the task to a file and run

    $FACTORY_KIT/foreman/codex-run.sh /private/tmp/t7-wt /private/tmp/t7-task.md

in the background. It calls `command codex exec --cd <worktree>` (never a `codex` shell function), marks the run as
a factory session so the guards in `~/.codex/hooks.json` apply, and refuses protected folders. Inside Codex,
questions go through the ask-me skill with `--timeout 5` and `--wait`.

`CODEX_SANDBOX=1` in factory.env (off by default) adds `--sandbox workspace-write`: the build can write only its
worktree, but git commit and ask-me don't work inside it (read-only git directory, no network); commit after it ends.

## Never

- Build in the foreground, or start work without the owner's `F: go T<n>`.
- Change a protected folder (PROTECTED_DIRS, and the kit itself): the guards block git writes and edits there.
- Send to other people, delete, pay, or push to someone else's repo from a channel task: say it needs the owner's
  approval at the Mac. Messages to the owner's own chat (replies, ask-me) are fine.
