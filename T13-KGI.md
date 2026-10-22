# T13 KGI

## KGI

- Metric: the kit installs the whole factory on another Mac from a config file alone
- Target: on a clean macOS user account, with only `factory.env` filled in, `install.sh` and then `doctor.sh` pass; every test passes (channel, guard, watchdog, ask-me, safe-push, check_task, the new apply_patch guard tests); and a fake-Beeper `F:` task reaches the foreman session and gets a spec reply. The repo contains 0 personal values.
- Deadline: 2026-11-15
- How measured: `tests/test_clean_install.sh` runs the install into a temporary HOME with a fake Beeper server. `scan_private.py` fails on any chat ID, phone number, name, token or `~/clawd` path in tracked files.
- Based on: T13 research. The official Telegram channel plugin keeps tokens in a per-user `.env` with a state directory per instance. Real failures: channels going silent after updates, dev-channel dialogs blocking restarts, and 24,008 secrets leaked in MCP configs (`~/research/t13-kit-foreman/report.html`).

## KPIs

| KPI | Target | How measured | Based on |
|-----|--------|--------------|----------|
| Pieces shipped | foreman.sh, channel, watchdog plus start script and pin, factory_guard, security hooks, ask-me, ask_me_stop, safe-push, CLAUDE.md/AGENTS.md/TASKS.md templates, remote WORKFLOW variant | file list test | inventory.md |
| Codex file guard | blocks `apply_patch` edits into protected folders (today this passes unchecked) | test_guard_apply_patch | Codex docs, issue 27833 |
| Skills in both tools | each kit skill linked into ~/.claude/skills and ~/.agents/skills | test_install | vercel-labs/skills |
| Personal values in repo | 0 | scan_private.py in tests | GitGuardian MCP leak study |
