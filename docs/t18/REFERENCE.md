# T18 Reference

REA: not applicable - Codex is Rust and its source (openai/codex 86e17e98) was read by hand with file:line in ~/research/t18-codex-watchdog/sources/repo/; our watchdog is our own code.

Labels: **[O]** observed (in code or on this Mac), **[I]** inferred, **[U]** unknown.

## Reference system

- Name: OpenAI Codex CLI / app-server 0.163.0 (the rollout files every client writes) and its documented hooks.
  No existing watcher had evidence of working for "finished, stopped silently, idle" on all clients, so there is
  no system to copy; we read the format Codex itself writes.
- Pinned: openai/codex 86e17e98c7f901277aeeb4649cf1b01462b082f2 (sources/repo/COMMIT). File digests (sha256, first 16):
  protocol.rs `9dc4ca7be8551397`, exec_events.rs `dafa872d7e86a099`, exec lib.rs `cb9a1eb6239ece15`,
  docs hooks `08742812479f994a`, local T9 timeline `73ff33873c0392c1`.
- Licence: Apache-2.0; reuse: learn only (no Codex code is copied; we parse its JSONL).

## Observed behaviour

1. **[O]** Rollout turn events are `task_started` (alias `turn_started`), `task_complete` (alias `turn_complete`)
   and `turn_aborted` with `reason` in snake_case `interrupted`, `replaced`, `review_ended`, `budget_limited`
   (protocol.rs:1519, :1528, :4393-4399, :6260).
2. **[O]** `codex exec --json` prints `thread.started`, `turn.started`, `turn.completed`, `turn.failed`, `item.*`,
   `error` (exec_events.rs:13-36); exec exits 1 when a non-retried error or a failed/interrupted turn was seen
   (lib.rs:1343-1359, :1404-1405).
3. **[O]** Stop hook input: `session_id`, `cwd`, `transcript_path`, `turn_id`, `stop_hook_active`,
   `last_assistant_message`; matching hooks run concurrently; new hooks are skipped until trusted in `/hooks`
   (docs hooks :576-605, :1282-1322, :26-27, :87-88).
4. **[O]** On this Mac (2026-10-22, read-only): `session_meta.source` is `exec`, `cli`, `vscode` (originator
   `codex-tui` or `Codex Desktop`) or `{"subagent": {"other": "guardian"}}` / `{"subagent": {"thread_spawn":
   {"parent_thread_id": ...}}}`. Of the two T9 "exec runs" that stopped at 17:55Z, the second
   (01a15f9a-6835) is a thread_spawn subagent of the first (01a15f9a-0e2a): one run, one stop to report.
5. **[O]** The live `codex app-server` (pid 21219) holds the rollout files of its loaded sessions open
   (`lsof -c codex`), while its own cwd is not the session's folder; the `codex --cd <dir>` TUI process has the
   session folder as cwd. So "process alive" is decided by the open rollout first, by cwd only for non-host processes.
6. **[O]** Codex Desktop appended imported history to old rollouts at 2026-10-22T01:09:21Z with turn ids
   `external-import-turn-*` (rollout 01a15c00-d1b3): not runs, must not be reported.
7. **[O]** Rollouts are up to 12 MB and a session can stay in an older YYYY/MM/DD folder for weeks (a 2026-08-15
   session wrote until 2026-09-04), so the watcher selects files by mtime, not by folder date.
8. **[I]** A turn that ask_me_stop sends back (decision block) continues in the rollout; the watcher therefore
   tells "finished" from the rollout's `task_complete` and uses the Stop hook record only for sessions without a
   rollout (e.g. `codex exec --ephemeral`).
9. **[U]** Whether Stop fires in `codex exec` and in the VS Code client (ROADMAP risk): the rollout watcher does not
   depend on it.

## What we take

- Rollout event names and session_meta fields (1, 4, 6) for the watcher; exec events and exit code (2) for
  codex-run.sh; Stop hook fields (3) for codex_watch_stop.py; open-file liveness (5) for R3.

## Not covered

- Rollout format changes in later Codex versions (unknown records are ignored; doctor.sh reports when no rollout
  is readable).
