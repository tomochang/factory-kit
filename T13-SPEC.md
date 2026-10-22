# T13 Spec: factory-kit ships the whole foreman flow, for Claude Code and Codex

## Request

"Factory-kit you made is not enough. It's not included each flows .sh and foreman functions. Why not?" + "Including codex one too." + "11,13 also includes the codex skill" (WhatsApp, 2026-10-22)

## Requirements

| # | Requirement |
|---|-------------|
| R1 | Copy into the kit and generalise: foreman.sh, start-watchdog.sh + pin, factory_guard.py, mcp.json and workspace-settings templates, channel/whatsapp-channel.mjs (server instructions from a template), watchdog.py, ask-me.py, ask_me_stop.py, safe-push.sh, security-bash.sh (generic sections 1–6 only), security-file-write.sh, with their existing tests. clawd keeps its own copies unchanged. |
| R2 | All personal and machine values come from a gitignored `factory.env` (from `factory.env.example`): OWNER_NAME, CHAT_ID, TOKEN_FILE, BEEPER_BASE, WORK_DIR, PROTECTED_DIRS, TZ, ASK_ME_STATE. The installer writes absolute paths into the generated files. |
| R3 | Codex: the guards also parse `apply_patch` paths (`tool_input.command`), so file guards work under Codex. One set of guard scripts is used for Claude Code (`~/.claude/settings.json`) and Codex (`~/.codex/hooks.json`). `codex-run.sh` starts `command codex exec --cd <worktree>` for builds. The Claude foreman stays the inbox, because Codex has no channel push. ask-me is used with `--timeout 5` and `--wait`. |
| R4 | Skills: each kit skill (imozuru, plus new foreman, ask-me and safe-push skills) is kept once in the kit and linked into ~/.claude/skills and ~/.agents/skills. It skips names that already exist. |
| R5 | `install.sh` prints the lines it would add to global files (~/.claude/settings.json, ~/.codex/hooks.json, shell rc). It writes them only with `--apply` (after taking a backup), and supports `--uninstall`. It also prints the Codex `/hooks` trust step. |
| R6 | `doctor.sh` checks Beeper reachable, the self-chat check, hook trust, Claude/Codex versions, the dev-channel flag, and the watchdog running. The watchdog also alerts when the channel goes quiet. |
| R7 | WORKFLOW.md gets a remote variant (WhatsApp `F: go`, ask-me, safe-push) next to the terminal-only one. Transport is Beeper/WhatsApp in v1, behind one module, so another transport can replace it later. |
| R8 | The repo stays private. clawd-only policy (security sections 7–8, act.sh rules, clawd Codex hooks) is not shipped. |

## Done when

- [ ] Clean-install test in a temporary HOME with a fake Beeper passes end to end (KGI)
- [ ] All shipped tests pass, plus the new apply_patch guard, install, doctor and scan tests
- [ ] scan_private.py finds nothing in tracked files
- [ ] Pushed to the private repo with safe-push

## Out of scope

- Changing ~/clawd or his running foreman (switching his own Mac to the kit's copies is a later, separate choice)
- Telegram or other transports (v1 is Beeper only)
