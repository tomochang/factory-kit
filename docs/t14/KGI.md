# T14 KGI

## KGI

- Metric: the kit works for a user whose work folder is not ~/factory, and every push is tested by CI
- Target: clean-install test with WORK_DIR set to a non-default folder passes, and its workflow files name that folder (0 hits for "~/factory" in rendered files); the GitHub Actions run of tests/run_all.sh is green on the PR; LICENSE present
- Deadline: 2026-10-29
- How measured: tests/test_clean_install.sh (custom WORK_DIR case) and the Actions run on the t14 PR
- Based on: his evaluation `~/clawd/projects/factory/docs/factory-kit-evaluation-2026-10-22.md` fix 4 (WORKFLOW hardcodes ~/factory although WORK_DIR is configurable; no CI; no LICENSE; T13 files at root). Framework-tier repos in that comparison (everything-claude-code 13 workflows, compound-engineering 5, beads 21) all run CI.

## KPIs

| KPI | Target | How measured | Based on |
|-----|--------|--------------|----------|
| Hardcoded "~/factory" in installed files with a custom WORK_DIR | 0 | test_clean_install.sh grep | evaluation fix 4 |
| CI | green on PR | GitHub Actions | evaluation comparison table |
| Task files at repo root | 0 (moved to docs/t13, docs/t14) | test_files | evaluation fix 4 |
