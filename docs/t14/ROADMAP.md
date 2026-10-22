# T14 Roadmap

| Milestone | What is usable after it | Serves | Test |
|-----------|-------------------------|--------|------|
| M1 | Lock + tests (custom WORK_DIR clean install, file layout, LICENSE, workflow file present) | KGI | tests fail before build |
| M2 | Rendered workflow, docs moved, LICENSE, CI workflow | KGI | run_all.sh |
| M3 | Branch pushed, PR open, CI green | CI KPI | Actions run |

## Risks

- macOS runners lack Beeper; the clean-install test already uses a fake Beeper, so it should run there. If a test needs his Mac, CI skips it with a stated reason.
