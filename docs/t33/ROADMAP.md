# T33 Roadmap

| Milestone | What is usable after it | Serves (KGI/KPI) | Test that proves it |
|-----------|-------------------------|------------------|---------------------|
| M0 REA + probe | REFERENCE.md on OpenHands StuckDetector (pinned commit, sha256, MIT) and a probe report on this Claude Code version. The probe answers: do PreToolUse hooks fire in background Agent and Workflow subagents with `agent_id`? Does a deny in a subagent let it continue? What does `continue: false` from a subagent's hook do (does it stop only the subagent)? Is `maxTurns` honoured now? | KGI (enforcement point is real) | probe script in a scratch session; its output is saved as a fixture |
| M1 Meter and cost log | `run_budget.py` counts tool uses, minutes and transcript tokens per agent_id, and writes `runs.jsonl` at SubagentStop. `run-budget report T{n}` prints the Cost line. Nothing is blocked yet (observe only). | K1, K2 | token, report and cost-log tests on the two real fixture transcripts (80350/98977, 19/35) |
| M2 Caps and ladder | warn, block (save set allowed) and stop for context, tools, minutes and CI rounds, with the default caps and config overrides | KGI, K3, K5 | ladder tests per cap; block-stage save-set test; overshoot within 10% on fixtures |
| M3 Stall stop | repeat and same-error rules, the polling exemption, and reset on a changed result | K4 | stall tests incl. the OpenHands #762 polling case and a long-output case |
| M4 Report enforcement | the reply guard requires a `Cost:` line on final T reports; factory agent types with `maxTurns: 200`; foreman instruction lines | KGI, K1 | reply guard tests; live check in a scratch session (`echo x` ×10 → `stall:repeat`, partial commit kept, row in report) |
| M5 Delivery | factory-kit PR (`t33-run-budget`) and clawd PR (`factory/t33-run-budget`), all checks green, `check_task.py` run | delivery | CI on both PRs; `gh pr checks` all successful |
| M6 KGI measurement | `run-budget audit` run 14 days after his install, result sent in a report | KGI | audit output: 0 silent overruns, 0 missing Cost lines, ≥20 runs |

## Risks

- **Main risk: the enforcement point may not reach background subagents the way the docs say.** If a PreToolUse deny only makes a stubborn run retry, or if `continue: false` from a subagent's hook stops the whole foreman session, the ladder needs a different stop. M0 probes this first. Fallback: the block stage keeps denying (every tool except the save set), so the run can only hand back. The watchdog then reports any run still alive past the hard minutes.
- **Delivery is blocked by safe-push** (T30's current blocker): safe-push cannot create a new remote branch, because it fetches `origin/<branch>` first. Default: the build commits to local worktree branches, and M5 waits until either T30's safe-push fix lands or he creates the two branches at the Mac (one `gh api .../git/refs` command per repo). The final report will say which.
- **Wrong stops of good long runs** (TestEvo-Bench: a tight cap cut scores from 59.5% to 18.8%; Cursor and OpenHands #762 false positives). Mitigation: caps above everything seen this week, the per-task `budget: x2/x3` tag, the polling exemption, and K4 is tracked.
- **Transcript format is undocumented** (75979: "undocumented internals, so parse defensively"), and the final row can lag. Mitigation: incremental reader with tolerant parsing, a 5 s poll at SubagentStop, and fixture tests from real transcripts. When parsing fails the row says `unknown`, never zero.
- **Hook cost and failure**: a slow or crashing hook on every tool call would hurt every run. Mitigation: R12 (5 s timeout, incremental read, fail-open except in the block stage).
- **Context tokens vs spend**: the cap uses the notification's number (context size). Cumulative spend is only logged until the audit has enough runs to set a grounded cap (2 runs so far).
