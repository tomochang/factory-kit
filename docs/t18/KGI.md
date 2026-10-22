# T18 KGI

## KGI

- Metric: share of Codex runs (codex exec and the VS Code/TUI client) whose finish, stop/crash or idle he hears about on WhatsApp in time, without false alarms
- Target: 100% of the replayed cases told within N+1 minutes of the transition (N = idle threshold, default 10 min), at most one message per transition, and 0 false "idle/stopped" alerts on runs that are still working (long thinking, long tests, background subagents)
- Deadline: 2026-11-05
- How measured: `tests/test_codex_watch.py` replays rollout fixtures for each case (T9 idle after task_complete, the two T9 exec runs that stopped silently at 17:55Z, turn_aborted, a long quiet tool run with the process alive, parallel sessions, guardian subagent rollouts); then a live check of one codex exec and one VS Code session on his Mac.
- Based on: T9 on 2026-10-22, where Codex finished at 03:58 JST and sat idle for 6 h without anyone hearing (`~/research/t18-codex-watchdog/sources/local-t9-rollout-timeline.md:22`), and two exec runs stopped with no final record (:8). False-alarm reports: #32634, #93672, codexmon thresholds (report.html).

## KPIs

| KPI | Target | How measured | Based on |
|-----|--------|--------------|----------|
| Finished runs told | 100%, with cwd + first line of the last message | Stop hook test + rollout replay | Codex hooks docs: Stop fields (`openai-codex-docs-hooks.md:1282-1322`) |
| Silent stops / crashes told | 100% | rollout replay (no task_complete, file quiet, no process for cwd) | T9 exec runs 17:55Z |
| Idle after finish told | once, after N min | rollout replay | T9 VS Code session |
| False alarms on working runs | 0 | replay of long-quiet-but-alive cases | #32634, #93672 |
| Repeat messages per transition | ≤ 1 | state-file test | tmux-agent-monitor |
