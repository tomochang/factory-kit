# Factory foreman watchdog

`watchdog.py` checks every 15 seconds whether the factory foreman is running (any process whose command line
contains `server:whatsapp`, from `ps -axo pid=,command=`; the watchdog's own process never counts) and remembers
its PID. Messages go to the owner's own chat through `../scripts/ask-me.py --from watchdog --tell`:

- running -> stopped: `Factory foreman stopped at YYYY-MM-DD HH:MM` (time zone `TZ` from factory.env)
- stopped -> running: `Factory foreman is running again`
- a restart between two checks (new PID): `Factory foreman restarted at ...`
- the foreman runs but the channel has not read the chat for 10 minutes (`--quiet-after`): `Factory channel
  quiet: ...`, and `Factory channel is reading messages again` once it reads again. The channel writes
  `<ASK_ME_STATE>/channel.heartbeat` after every successful read.
- nothing at startup or while nothing changes; a failed send is logged and the watchdog keeps running.

It also watches Codex runs (T18): every check it reads the rollout files Codex writes for each session
(`CODEX_SESSIONS`, default `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, only files changed in the last 24 h), so
`codex exec`, the VS Code/Desktop client and the TUI are all covered:

- `Codex finished: <repo> - <first line of the last message>` when a turn ends (`task_complete`)
- `Codex idle <N> min after finishing: <repo>` once, when a finished session (and its subagents) stays quiet
  `CODEX_IDLE_MIN` minutes (default 10)
- `Codex stopped without finishing: <repo>` when a turn has no `task_complete`, the rollout has been quiet N minutes
  and no codex process holds it open or works in its folder (`lsof -c codex`; `codex app-server` hosts count only
  through the rollouts they hold open). A run whose process is alive is never reported, however long it is quiet.
- `Codex interrupted (<reason>): <repo>` on `turn_aborted` that lasts 30 s (a "replaced" turn followed by a new one
  is not reported)
- guardian and subagent rollouts are never reported themselves; their activity keeps the parent from being idle.
  Turns Codex Desktop imported (`external-import-*`) are not runs.
- one message per transition, remembered in `<ASK_ME_STATE>/codex-watch/state.json`; the first start takes a
  baseline and sends nothing. Only one line of the last message is sent, with token-like strings redacted.
- `hooks/codex_watch_stop.py` (a Codex Stop hook, installed by `install.sh`, trusted in `/hooks`) records each
  finished turn in `<ASK_ME_STATE>/codex-watch/stops/`; the watchdog uses it for sessions it has no rollout for
  (e.g. `codex exec --ephemeral`).
- `foreman/codex-run.sh` adds `--json`, keeps the events beside the task and sends `Codex build failed: <task>`.

`foreman/foreman.sh` starts it once through `foreman/start-watchdog.sh`, and only when `watchdog.py` matches the
pin in `watchdog.sha256` (the kit folder is protected from factory edits). After reviewing a change:

    shasum -a 256 watchdog/watchdog.py | cut -d' ' -f1 > watchdog/watchdog.sha256

A watchdog that is already running keeps its old code: `start-watchdog.sh` names an older running version with
the restart command (`pkill -f .../watchdog/watchdog.py`, then start it again).

Usage: `python3 watchdog.py [--interval 15] [--quiet-after 600] [--once] [--no-codex]`. Stop it: `pkill -f factory-kit/watchdog/watchdog.py`.
Tests: `tests/test_watchdog.py` (fake process list, sender and heartbeat only), `tests/test_codex_watch.py`
(replays rollout fixtures from real runs in `tests/fixtures/codex/`), `tests/test_codex_hook.py`,
`tests/test_codex_run_watch.py`.
