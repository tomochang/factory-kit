# factory-kit

A software factory for Claude Code and Codex that you can drive from your phone. Every task goes through the same
ten steps: number → real-use research (imozuru) → KGI → spec and roadmap → suggest → refine → lock on your go →
REA on the best existing system → tests first → build. Full text: `workflow/WORKFLOW.md` (terminal) and
`workflow/WORKFLOW-remote.md` (WhatsApp).

The kit holds the whole foreman flow:

| Piece | What it does |
|-------|--------------|
| `foreman/foreman.sh` | starts the foreman: one Claude Code session in your work folder whose inbox is your own WhatsApp chat |
| `foreman/channel/` | the `whatsapp` channel (MCP, `claude/channel`): `F:` messages become tasks, the reply tool answers; Beeper is one module (`beeper-transport.mjs`) |
| `foreman/factory_guard.py`, `hooks/security-*.sh`, `hooks/guard_bash.py` | guards for Claude Code and Codex: no writes (git or any other tool) or edits in protected folders, no destructive commands, no secret reads, no secrets in files; Bash commands are parsed (wrappers, `bash -c`, `eval`, quoted text as data, ported from cc-safety-net, see NOTICE); they read Codex `apply_patch` edits too |
| `hooks/ask_me_stop.py` | Stop hook: a turn that ends with a question is sent back once, to ask on your phone instead |
| `hooks/reply_guard.py` | the foreman's WhatsApp replies, enforced: a reply that is too long, does not start with what you must do (`You:` / `Nothing needed` / `Got it`), shows a local file path, claims done/green while the PR's checks are not all green (`gh pr checks`), or comes as a burst is refused with a rewrite hint; the foreman cannot end its turn while a message of yours has no reply. Limits in factory.env (`REPLY_*`) |
| `hooks/run_budget.py`, `scripts/run-budget` | a budget for every background run of the foreman (Agent and Workflow subagents; the foreman itself is never capped): context tokens, tool uses and minutes warn at 300k / 150 / 45 and block at 400k / 200 / 60, a repeated call with the same result is denied from the 3rd time and blocks the run at the 6th, the same error 3 times blocks it, 2 failing CI rounds hand it back. A blocked run may only commit, write PARTIAL.md and hand back `STOPPED: <reason>`; 10 calls later it is ended. Each run gets a cost row in `RUN_BUDGET_DIR/runs.jsonl`; `run-budget report T<n>` prints the `Cost:` line, and a final T report without it is refused. `run-budget audit --since <date>` checks the KGI (docs/t33). Agent types `factory-builder` / `factory-researcher` (maxTurns 200) go into the work folder. Caps in factory.env (`RB_*`) |
| `scripts/ask-me.py` | asks you on your phone and returns your reply; `--tell` for reports; `--check` for doctor |
| `scripts/safe-push.sh` | pushes a worktree's commits safely (clean, rebased, tested) |
| `scripts/todo-req.py` | files one request (`todo-request/1` JSON in `<WORK_DIR>/todo-outbox/`) to change your todo or your own calendar; the factory never writes them itself, your own applier outside the factory checks and applies it |
| `watchdog/watchdog.py` | messages you when the foreman stops, restarts or its channel goes quiet, and when a Codex run finishes, stops without finishing, is interrupted or sits idle after finishing (started pinned by `foreman/start-watchdog.sh`) |
| `hooks/codex_watch_stop.py` | Codex Stop hook: records each finished turn for the watchdog (trust it once in Codex `/hooks`) |
| `foreman/codex-run.sh` | runs a build in Codex: `command codex exec --json --cd <worktree>` (events in `<task>.codex.jsonl`, exit code kept, "Codex build failed: <task>" on failure); `CODEX_SANDBOX=1` in factory.env adds `--sandbox workspace-write` (then git commit and ask-me don't work inside the build) |
| `skills/` | `imozuru`, `foreman`, `ask-me`, `safe-push`, linked into both `~/.claude/skills` and `~/.agents/skills` |
| `install.sh`, `doctor.sh` | install (dry run first) and check |

All personal and machine values live in one file, `factory.env`, which git ignores. `scan_private.py` keeps
personal values out of the repo.

## Needs

macOS, Claude Code 2.1.x (channels), Beeper Desktop with its API turned on and your WhatsApp in it, Node 18+ and
npm, Python 3.9+, git, jq. Codex CLI only for Codex builds. For the tests: pytest (`python3 -m pip install --user pytest`).

## Install (both tools)

1. Clone and fill in your values:

        git clone <this repo> ~/factory-kit && cd ~/factory-kit
        cp factory.env.example factory.env && chmod 600 factory.env
        # edit factory.env: OWNER_NAME, CHAT_ID (your own "message yourself" chat), TOKEN_FILE, PROTECTED_DIRS, TZ
        mkdir -p ~/.config/factory-kit && pbpaste > ~/.config/factory-kit/beeper-token && chmod 600 ~/.config/factory-kit/beeper-token

2. See what the installer would change (nothing is written):

        bash install.sh

   It prints the exact lines for `~/.claude/settings.json` (the Stop hook), `~/.codex/hooks.json` (the guards and
   the Stop hook), your shell rc (`FACTORY_KIT` and a `foreman` alias), the skill links and the work folder files.

3. Apply it (each global file is backed up first as `<file>.factory-kit-backup-<time>`):

        bash install.sh --apply

   Skill names that already exist in `~/.claude/skills` or `~/.agents/skills` are skipped, and existing work
   folder files are kept.

4. **Codex: trust the hooks.** Codex skips new or changed hooks until you trust them. Start `codex`, type
   `/hooks`, review the factory-kit hooks and trust them. Do this again after the kit's hooks change.

5. Check:

        bash doctor.sh

   It checks factory.env and the token file, that Beeper answers and that `CHAT_ID` is your own chat, the Claude
   and Codex hooks and the Codex trust records, the Claude Code / Codex versions, the dev-channel setup and the
   watchdog. It sends nothing.

The work folder is `WORK_DIR` in factory.env (default `~/factory`). install.sh renders the kit's `workflow/`
files and `templates/factory/` into it with that folder filled in, so the workflow names your folder; edit them in
the kit and run `install.sh --apply` again to update them.

To take the kit out again: `bash install.sh --uninstall` (removes only what the kit added, including the rendered
`workflow/` folder; your TASKS.md and task repos stay).

## Use

Start the foreman and keep its terminal open:

    ~/factory-kit/foreman/foreman.sh

The first start asks about development channels: choose "I am using this for local development". Then, from your
phone, send yourself `F: <task>` in WhatsApp. The foreman numbers it, researches, and replies with a short spec
ending "Reply F: go T<n>". Nothing is built before your `F: go T<n>`. Builds run in background agents in git
worktrees; a build can also run in Codex:

    ~/factory-kit/foreman/codex-run.sh /private/tmp/t7-wt /private/tmp/t7-task.md

Without the phone, `cd ~/factory && claude` (your `WORK_DIR`) (or `codex`) follows the same workflow in the terminal.

### Check it works

Send `F: ping` from your phone while the foreman runs: it should answer in your chat within a minute. Your message gets 👀 as soon as the channel reads it (no model involved) and ✅ once the foreman has replied.

## Tests

    bash tests/run_all.sh

Runs `scan_private.py`, the channel tests (`node --test`), the Python tests (pytest: config, guards with Codex
`apply_patch`, install in a temporary HOME, doctor against a fake Beeper, ask-me, the Stop hook, safe-push,
watchdog, start-watchdog), the workflow checker's tests and `tests/test_clean_install.sh` (a clean install into a
temporary HOME with a fake Beeper: install, doctor, an `F:` message through the channel, the spec reply, then a custom `WORK_DIR`). Tests
never use your HOME, Beeper or agent configs. `foreman/channel/test/smoke-live.mjs` is a manual test that sends one
real message to your own chat.

CI: `.github/workflows/test.yml` runs `tests/run_all.sh` on a GitHub macOS runner for every push and pull
request (no secrets).

Task records: `docs/t<n>/` holds each kit task's locked KGI, spec, roadmap and reference (T13: the kit, T14:
work folder, docs, CI, licence).

## Update

    cd ~/factory-kit && git pull && bash install.sh --apply

Then trust any changed hooks in Codex (`/hooks`) and restart the foreman.

## Licence

MIT, see `LICENSE`.
