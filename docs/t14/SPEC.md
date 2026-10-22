# T14 Spec: factory-kit cleanup (work folder, docs, CI, LICENSE)

## Request

"Fix the hardcoded ~/factory path, move the T13 files, and add CI and a LICENSE." (WhatsApp, 2026-10-22), go: "t14 go". Delivered as a branch + PR ("branch if you finished").

## Requirements

| # | Requirement | Serves |
|---|-------------|--------|
| R1 | Workflow files (WORKFLOW.md, WORKFLOW-remote.md, workflow/README.md) and templates/factory/* use a placeholder for the work folder. install.sh renders them into WORK_DIR with the configured path instead of symlinking the kit's workflow folder. Re-running install updates them; --uninstall removes what it rendered. Default WORK_DIR stays ~/factory, so the default output is unchanged. | hardcode KPI |
| R2 | Move T13-KGI/SPEC/ROADMAP/REFERENCE.md to docs/t13/ (git mv, links updated); T14 docs live in docs/t14/. | root KPI |
| R3 | .github/workflows/test.yml: on push and pull_request, runs-on macos-latest, sets up Python 3 with pytest and Node 20, runs bash tests/run_all.sh. No secrets used. | CI KPI |
| R4 | LICENSE: MIT, copyright 2026 tomochang. README gets a Licence line. | LICENSE |
| R5 | scan_private.py still finds 0 personal values; all existing tests still pass. | KGI |

## Done when

- [ ] test_clean_install.sh has a custom-WORK_DIR case that passes and finds no "~/factory" in rendered files
- [ ] tests/run_all.sh ALL PASSED locally, and the Actions run is green on the PR
- [ ] Branch t14-kit-cleanup pushed with safe-push, PR opened against main (not merged)

## Out of scope

- Repo visibility (his call); guards (T15); ~/clawd
