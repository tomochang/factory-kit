# T18 Roadmap

| Milestone | What is usable after it | Serves | Test |
|-----------|-------------------------|--------|------|
| M1 | Rollout fixtures from real cases (T9 idle, silent exec stops, turn_aborted, long quiet working run, parallel and subagent sessions); REFERENCE.md (REA not applicable for Codex source in Rust; read the pinned source by hand with file:line) | KGI | fixtures |
| M2 | Tests first: replay, state file, no false idle, Stop hook, codex-run --json | KGI | tests fail before build |
| M3 | watchdog rollout watcher + messages + config | stops, idle | replay tests |
| M4 | Codex Stop hook + installer/doctor trust check; codex-run --json | finished | hook tests |
| M5 | kit branch + PR; clawd branch from a clone; live check after his merge | delivery | branch on origin |

## Risks

- Whether `Stop` fires in codex exec and in the VS Code client is unconfirmed; the rollout watcher covers both even if it does not.
- Rollout format may change with Codex updates; the parser ignores unknown records and doctor.sh reports when no rollout is readable.
