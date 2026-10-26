# T33 Spec: run budget, cost log and stall stop for every background run (clawd + factory-kit)

## Request

From T32, his pick: "T32 1". The recommendation he accepted, unchanged: "Each background run gets a token cap, stops after N rounds or when it repeats the same action, and its cost goes in the report."

Context from the foreman (2026-10-26): background subagents run 5–40 minutes, with 80k–300k subagent tokens and 13–142 tool uses each (seen this week).

## Best real case

No one system covers the whole request. The design copies four real cases (research: `~/research/run-budget/`, map.md, report.html, digest.html):

1. **OpenHands StuckDetector** (MIT, source available, on by default): it treats a run as stuck when the same action gets the same observation 4 or more times, or the same action errors 3 or more times (`sources/openhands-docs-stuck-detector.md:13-14`). The thresholds became configurable after users hit false positives: polling every 5 minutes for 15 minutes tripped the detector (`gh-openhands-sdk-762-stuck-thresholds.md:83`). **This is the REA reference** (`openhands-sdk-stuck_detector-py.md`, defaults in `openhands-sdk-conversation-types-py.md:151`).
2. **OpenRouter doom-loop ladder**: observe at 2 repeats, block at 3, stop at 6; "same" means identical inputs (`openrouter-doom-loop-detection.md:20,86`). Blocking returns an error to the model so that it can change course, and the stop leaves a conversation that can be resumed (`:54-55`). **OpenClaw** always stops after 3 identical tool errors, and keeps the broad detector off by default because of false positives (`openclaw-loop-detection.md:12,22`).
3. **Stripe minions**: at most 2 CI rounds, then the branch goes back to a human (`stripe-2026-minions-part2.md:109,111`).
4. **Claude Code's own hooks**: PreToolUse and PostToolUse fire inside subagents and carry `agent_id` (`claudecode-docs-hooks.md:255`). Background Agent results carry no usage (`:1929`), and `totalTokens` covers only the final request (`:1913`). The usage is in the subagent transcript, but the last row may not be flushed yet when SubagentStop fires (`gh-claude-code-75979-subagentstop-usage.md:86`).

What it measured:
- mini-SWE-agent: gains flatten at about a 50-step limit (`swebench-2025-mini-roulette.md:95`).
- CodeClash: the cost cap was "almost never reached" while turn caps were used up often (`arxiv-2511.00839-codeclash.txt:2082`).
- TestEvo-Bench: a tight $1 cap cut SWE-Agent from 59.5% to 18.8% (`arxiv-2607.02469-testevo-bench.txt:553`). Caps set too low kill good work.

Why we cannot rely on Claude Code's built-in caps:
- `maxTurns` was seen ignored: 72 turns with a cap of 10, and 6 turns with a cap of 2 (`gh-claude-code-41143...:87`, `gh-claude-code-79303...:61`).
- A turn-capped run was misreported as a model failure (`gh-claude-code-80848...:65`).
- `--max-budget-usd` and `--max-turns` exist only in print mode (`claudecode-docs-cli-reference.md:105-106`). Our foreman is interactive.

**Our own measurement** (`sources/local-2026-10-26-subagent-usage-check.md`): the completion notification's `subagent_tokens` equals the *last request's* total (input + output + cache), not the run total. Searcher A showed 98,977 in the notification but processed 369,318 tokens across its requests. So this week's "80k–300k" figures are final context sizes. The cost log records both numbers.

## Default caps (per run; overridable per agent type and per task)

| Cap | Warn (hook tells the run to wrap up) | Hard stop | Source |
|-----|------|-----------|--------|
| Context tokens (last request total, the number in the notification) | 300k | 400k | warn = the most seen this week (300k, foreman's note); the +33% to the hard stop is an assumption |
| Cumulative tokens (sum of all requests, de-duplicated, cache included) | logged only | none (logged only) | ratio context:cumulative was 1:3.2 and 1:3.7 on our 2 runs (local check). Too few runs to set a cap, so this is an assumption to revisit in the KGI audit |
| Tool uses | 150 | 200 | the most seen this week was 142 (foreman's note); headroom is an assumption |
| Wall-clock minutes | 45 | 60 | the longest seen this week was 40 (foreman's note); headroom is an assumption |
| Same call repeated (same tool and identical input, same result) | 3rd repeat is denied with a "change course" reason | 6th repeat stops the run | OpenHands 4+ same action/observation; OpenRouter block@3, stop@6 |
| Same error repeated (same tool, input and error) | — | 3rd in a row stops the run | OpenHands 3+, OpenClaw 3 |
| CI rounds (push + `gh pr checks` failing) | — | after 2 failing rounds, hand back to the foreman | Stripe's 2 CI rounds |
| Agent turns (frontmatter `maxTurns` on factory agent types, a backstop only) | — | 200 | Claude Code's own fork cap is 200 (`gh-claude-code-96455...:57`) |

Repeats that look like polling (the same `gh pr checks`, `sleep`, or watchdog/status command whose *result changes*) never count. Only identical input with an identical result counts (OpenClaw: "Changed results, changed arguments, and successful calls reset the error streak", `openclaw-loop-detection.md:16-17`).

## Requirements

| # | Requirement | Serves (KGI/KPI) | Copied from (real case) |
|---|-------------|------------------|-------------------------|
| R1 | **Budget hook** `run_budget.py`, registered for PreToolUse and PostToolUse/PostToolUseFailure on every tool (`matcher: "*"`), acting only when the input has an `agent_id` (a subagent), so the foreman itself is never capped. For each run it keeps a state file in `~/factory/.runs/<agent_id>.json` with: first-seen time, tool-use count, the last few (tool, input hash, result hash), and the error streak. On each call it reads the subagent transcript (path from `transcript_path`: `<session>/subagents/agent-<agent_id>.jsonl`). It sums usage de-duplicated by message.id, and takes the last request's total as context tokens. | KGI, K1, K2 | Claude Code hooks in subagents (`claudecode-docs-hooks.md:255`); transcript usage (`75979:86`); de-dup (`80848:87`, `claude-agent-sdk-cost-tracking.md:42`) |
| R2 | **Ladder**, copied from OpenRouter observe/block/stop. **Warn**: at a warn cap, allow the call but add context telling the run to wrap up, commit its work in its worktree and hand back with its stop reason. **Block**: at a hard cap or a stall, deny every tool except the save set (git add/commit/status/diff in its own worktree, Write/Edit of `PARTIAL.md`, SubagentHandback), with the reason "run-budget: <reason>. Commit what you have, write PARTIAL.md (what is finished, what is left, how to resume), then hand back starting `STOPPED: <reason>`". **Stop**: if the run makes 10 more tool calls after the block (assumption), deny everything with `continue: false` and record `killed:<reason>`. | KGI, K3, K5 | OpenRouter ladder (`openrouter-doom-loop-detection.md:51-55`); Claude Code partial output (`claudecode-docs-subagents.md:304`) |
| R3 | **Never discard**: the hook never deletes, resets or cleans a worktree. The block stage always lets the save set through. The stop row records the worktree path and last commit sha, so the foreman can resume (`SendMessage` to the agent) or hand the work to a new run. | K3 | Claude Code "resume it to continue"; OpenRouter "can be resumed" |
| R4 | **Stall rules**: the same tool with an identical input hash and identical result hash: 3rd time denied (the reason asks for a different approach), 6th time stops. The same tool, input and error 3 times in a row: stop. A changed result or a success resets the count. Commands on a polling allowlist (`gh pr checks`, `gh run watch`, `sleep`, watchdog status) are exempt while their result changes. | K4 | OpenHands thresholds; OpenRouter `loopKey`; OpenClaw reset rule; OpenHands #762 polling false positive |
| R5 | **CI rounds**: the hook counts failing `gh pr checks` (or safe-push test failures) after a push in a run. After 2 failing rounds it blocks with the reason `ci-rounds`. | request "stops after N rounds" | Stripe's 2 CI rounds |
| R6 | **Cost log**: one JSON line per run in `~/factory/.runs/runs.jsonl`, written when the run ends. That is the SubagentStop hook, which polls the transcript up to 5 s for the final row (75979 caveat), or the next foreman PostToolUse/Stop when the run was killed. Fields: agent_id, T number, description, agent_type, model, start, end, minutes, tool_uses, context_tokens, cumulative tokens by type (input, output, cache_creation, cache_read), stop_reason (`completed`, `cap:context`, `cap:tools`, `cap:minutes`, `stall:repeat`, `stall:error`, `ci-rounds`, `killed:<reason>`, `unknown`), worktree, last_commit. The T number comes from the Agent call's description ("T33 …"), which the PostToolUse hook on `Agent` maps to the returned agentId. | K1, K2 | `claudecode-docs-hooks.md:1929` (async result has agentId and description) |
| R7 | **In the report**: `run-budget report T{n}` prints one phone-short line, `Cost: 2 runs, ctx max 98k, total 412k tok, 54 tools, 6 min, stops: completed`. The foreman's T report (WORKFLOW step 10) includes it. The WhatsApp reply guard denies a final T report (one that names T{n} with a finished run in the log not yet reported) when it lacks a `Cost:` line. | KGI, K1 | his words "its cost goes in the report"; reply guard pattern (T22) |
| R8 | **Config**: caps in `factory.env` (factory-kit) and `run_budget.conf` (clawd), with per-agent-type overrides and a per-task override. A task override is a `budget: x2` tag in the Agent description, max x3 (assumption). The foreman's instructions say when to use it (REA or long builds). | K4 | ralph-orchestrator config (`ralph-orchestrator-docs-configuration.md:418-422`) |
| R9 | **Backstop agent types**: `.claude/agents/factory-builder.md` and `factory-researcher.md` with `maxTurns: 200`. The foreman uses them for T builds and research. Tests check that the hook, not maxTurns, does the enforcing. | KGI | Claude Code frontmatter (`claudecode-docs-subagents.md:304`); not trusted alone (41143, 79303) |
| R10 | **Audit**: `run-budget audit --since D` implements the KGI and KPI checks above and prints a short table. | KGI, all KPIs | — |
| R11 | **Delivery** as in T22/T28: generic code in factory-kit (`hooks/run_budget.py`, `scripts/run-budget`, tests, settings template, docs) on branch `t33-run-budget` with a PR. His values in clawd `projects/factory/` (`run_budget.py`, `run_budget.conf`, `workspace-settings.json` hook entries, tests) on branch `factory/t33-run-budget` from a clone under /private/tmp, with a PR. Nothing is merged by the factory, and ~/clawd is never edited. No new repos. | delivery | T15/T22 rows in TASKS.md |
| R12 | **Hook safety**: the hook must never block the foreman or a run because of its own failure. It has a timeout of 5 s, and on any exception it allows the call and logs `hook-error`. Exception: in the block stage, a crash keeps the block (`onFailure` set per stage). Its own run time is under 100 ms on a 300k-token transcript (it reads the transcript incrementally from a saved offset). | KGI | Claude Code `onFailure` (`claudecode-docs-hooks.md:447`) |

## Done when

- [ ] Hook tests replay fixture transcripts. Each cap (context, tools, minutes) gives warn, then block, then stop at the right count. The block stage still allows git commit, PARTIAL.md and SubagentHandback.
- [ ] Stall tests: an identical call 3 times is denied and 6 times stops. The same error 3 times stops. Polling with a changing result is not stopped. An OpenHands #762-style case (poll 4 times, 5 min apart, changing output) passes.
- [ ] Token tests: on the two real transcripts from this session (copied as fixtures, file names and IDs only), context tokens = 80350 and 98977, tool uses = 19 and 35, and the de-duplicated cumulative = 258,260 and 369,318.
- [ ] Cost log tests: completed, capped, stalled, killed and hook-error runs each give one complete row. A late-flushed final transcript row is picked up.
- [ ] Report and reply guard tests: `run-budget report T{n}` gives the one-line Cost; a final T report without it is denied.
- [ ] Live check in a scratch session: a subagent told to run `echo x` 10 times is stopped as `stall:repeat` with its partial commit kept, and its row appears in `run-budget report`.
- [ ] Both PRs open with every check green. All existing tests in both repos still pass.
- [ ] KGI audit script runs on the log, and a dated measurement plan is written in the test file (the KGI is measured 14 days after install).

## Out of scope

- Dollar costs. Claude Code's USD figures are client-side estimates (`claude-agent-sdk-cost-tracking.md:15`), and he is on a plan, not per-token billing (assumption). Tokens only.
- Capping the foreman's own session.
- Codex runs (the T18 watchdog covers them; a later task can reuse the log format).
- Changing the watchdog. It keeps reporting finish, crash and idle, and the cost log adds the stop reason.
- Merging either PR, and installing into the live foreman (his steps after merge).
