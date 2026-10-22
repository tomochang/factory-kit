# T13 Roadmap

| Milestone | What is usable after it | Serves (KGI/KPI) | Test that proves it |
|-----------|-------------------------|------------------|---------------------|
| M1 | REA on the official Telegram channel plugin (Apache-2.0, af59ee3a) for config, state and token handling; REFERENCE.md | grounded design | evidence IDs present |
| M2 | factory.env + generated configs; scan_private.py | 0 personal values | test_scan, test_config |
| M3 | Pieces copied and generalised with their tests; guards parse apply_patch | pieces, Codex guard | shipped tests + test_guard_apply_patch |
| M4 | install.sh (print / --apply / --uninstall), skills linked for both tools, codex-run.sh, remote WORKFLOW | skills KPI | test_install in temporary HOME |
| M5 | doctor.sh, clean-install test with fake Beeper, safe-push to the private repo | KGI | test_clean_install |

## Risks

- Claude Code channel updates have broken bridges before; doctor.sh and the watchdog quiet-alert reduce the time to notice.
- Codex hook trust needs a manual `/hooks` step on each Mac.
