# T18 Spec: the watchdog also watches Codex runs (clawd + factory-kit)

## Request

"codex can't use watchdog now? improve if it's correct" (WhatsApp, 2026-10-22). He also said that factory fixes go to both clawd and factory-kit as branches.

## Finding

Correct. watchdog.py only matches `server:whatsapp` (:32). codex-run.sh starts `codex exec` with nothing watching it. Codex's `notify` is already used by another program and is marked legacy. T9 ran in the VS Code client, so a watcher keyed on process names would have missed it as well.

## Requirements

| # | Requirement | Serves | Copied from |
|---|-------------|--------|-------------|
| R1 | Rollout watcher in watchdog.py: each interval, scan `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` changed in the last 24 h. Per session, read the last records and `session_meta` (source, cwd). Skip guardian/subagent rollouts. States: working, finished (last `task_complete`), aborted (`turn_aborted` + reason), stopped (no `task_complete`, file quiet ≥ N min, and no codex process whose cwd matches). | stops, idle KPIs | research: rollout event names (protocol.rs) |
| R2 | Messages via ask-me --tell, one per transition, kept in a state file: "Codex finished: <repo> - <first line>", "Codex idle <N> min after finishing: <repo>" (once), "Codex stopped without finishing: <repo>", "Codex interrupted (<reason>): <repo>". No prompt text beyond one line of the last message; no secrets. | repeat ≤ 1 | tmux-agent-monitor |
| R3 | No false idle: while the last record is not `task_complete` and a codex process for that cwd is alive, it is "working", however long it is quiet. Only a finished-and-quiet session is "idle". | 0 false alarms | #32634, #93672 |
| R4 | Codex `Stop` hook (a second entry beside ask_me_stop in ~/.codex/hooks.json, which runs concurrently): writes the turn to the state file so the watcher can tell him at once. The installer prints the `/hooks` trust step; doctor.sh checks that it is trusted. `notify` is not touched. | finished KPI | Codex hooks docs :26-27, :87-88 |
| R5 | codex-run.sh adds `--json`, saves the JSONL beside the task, and keeps the exit code. Exit ≠ 0, `turn.failed` or `error` produces "Codex build failed: <task>". | stops KPI | exec_events.rs, lib.rs exit codes |
| R6 | N (idle minutes) and the sessions folder come from factory.env (CODEX_IDLE_MIN=10, CODEX_SESSIONS=~/.codex/sessions). | config | T13 R2 |
| R7 | Delivery as in T15: a factory-kit branch `t18-codex-watch` with a PR, and a clawd branch `factory/t18-codex-watch` from a separate clone (never ~/clawd). The watchdog's pinned sha256 is updated on both branches. If a push is refused, the branch is handed over. Nothing is merged by the factory. Installing the hook and restarting the watchdog are his steps after merging. | his "branch" request | his rule |

## Done when

- [ ] Replay tests for every case in the KGI pass; existing watchdog tests still pass
- [ ] Live check on his Mac: one codex exec run and one VS Code session each produce the expected messages (reported; done after his merge if the hook needs trust)
- [ ] Both branches pushed (or handed over), PR open, CI green where T14's CI exists; check_task result reported

## Out of scope

- Answering or driving Codex from WhatsApp (Codex has no channel)
- Changing `notify`
