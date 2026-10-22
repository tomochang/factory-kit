# T13 Reference (REA)

Labels: **[O]** observed in code, **[I]** inferred, **[U]** unknown.

## Reference system

- Name and why it is the best real case: Anthropic's official Telegram channel plugin
  (`anthropics/claude-plugins-official`, `external_plugins/telegram`). It is the only published Claude Code
  channel that is installed on other people's machines with per-user config, and the research
  (`~/research/t13-kit-foreman/map.md`, claim 3) found no other channel + guard + watchdog kit with source.
- Pinned: commit `af59ee3a1051a29dda4a2e7a2af221aa457ae08b`, tree `external_plugins/telegram` =
  git tree `4c6700917560056b68748af3b6be457279b6bb09`. File manifest (`shasum -a 256` of every file, sorted)
  at `/private/tmp/t13-ref/telegram-manifest.sha256`, sha256
  `0b460533084cc4394e79c958ada8cc2fa2e04050f94f291f98d8b9eb552823f7`. REA root digest
  `0c00ab0aa8c8fb5825a45082063b5d7ae9e89ae43d5b72b9cf84234a9adce109`.
  `server.ts` sha256 `d902d7195a527b579b889cfdbf251fc24fd224a68e319ac6b7c3b691515c3458`,
  `skills/configure/SKILL.md` sha256 `16b06bf2ac5ede250d0c620ce4e6cf3878c9704246b8f52390fde5f280a17f73`,
  `.mcp.json` sha256 `0ee637b9a76aa35b3b9eb9a76b4b8fb036bc61651711e1af72badedde5e3c875`.
- Licence: Apache-2.0 (`LICENSE` sha256 `dfd016a63459229ceb791be757c3bede084fb22303c36b61342db1b8f58cc450`).
  Reuse: **learn only**. No code from it is copied into this kit, so no notice is needed; the kit's channel is
  the factory's own Beeper channel, generalised.

## REA runs

All runs: `npm_config_cache=/private/tmp/t13-ref/npm-cache npx -y rea-agents@6.7.0 ...`, evidence kept outside
the repo in `/private/tmp/t13-ref/`.

- `import-reference-source /private/tmp/t13-ref/repo/external_plugins/telegram` → HistoricalSourceGraph, root sha256 `0c00ab0a...e109`, 15 entries,
  11 import relationships, `vcs.head` = the pin. Saved at `rea-import-full.json`
  (sha256 `dc467f1d328fdbed5f951cf4391c4d0d1403d41111d32d3e382ea5fc6f7679cf`). Result: ok, but
  `inventory_state: partial` and no `ev_` ID (this command emits a graph, not Evidence).
- `analyze-javascript-application /private/tmp/t13-ref/repo/external_plugins/telegram --artifact-format directory` →
  `ev_636de8be2a9a863703a86565e33c49d4dea8c868e0b4479a22c661d59bde0fe9`, saved at `rea-js-app.json`
  (sha256 `8e7cc77955aae96535ab548e10de34a421da8a3e1062bc54b577e67b3cbe1a8c`). 1 parsed source file
  (server.ts), 2923 semantic nodes, 964 unknowns. Result: ok, confidence `derived`, semantic coverage partial.
- `trace-application-feature` (string seeds) on that Evidence: `TELEGRAM_BOT_TOKEN`
  `ev_bf005c830cef43b7d1182302d268595a345c8c22a46726cc848871ae42e467be`, `TELEGRAM_STATE_DIR`
  `ev_b09eb29717b43e9ce21a02a4468bda7fe4ad5b63b7aed350019d35f1a1729e46`, `notifications/claude/channel`
  `ev_eb9a16653a250e8ae2da08240afe1c9797f65d948b788a7470b9e9474a757daa`, `access.json`
  `ev_6cea5a02f62a12b1bfaeb7c8bd1767a670ab8402810c02a249c1a1e898f6c75c`, `claude/channel`
  `ev_9dee751377868b91ce6611359ac36c582af1199479394a0f0d6910abf11d9e65`. Result: **no-match** for all five
  (the application graph holds only 29 module-level nodes). REA says this "is not evidence that the feature is
  absent": recorded as missing coverage.
- `trace-javascript-semantics` (forward-influence) on semantic nodes of the same Evidence:
  - token env read (server.ts:44) → `ev_9b9ac075e81a008aa213c3703388c62f1d27f7b34b3e8789ec8839f68d9fc681`
    (`sem-token.json`, sha256 `ceef199746dc0686f1df53b1c84999fc6f2523afbadfe21207aec74a43dbcf69`), 1 node, partial.
  - `STATE_DIR` binding (server.ts:27) → `ev_b7566a5abfe384758660d2825719c53ef37d479eee17564576236310ba92f504`
    (`sem-statedir.json`, sha256 `b5d433dfb4e75bf6df1a5008eb8f699625d72a892e5ba0b3136b560be3c93efb`),
    19 nodes / 18 relations reaching lines 29-31, 55-56, 62, 148 (`assertSendable`), 213 (`saveAccess`).
  - `ENV_FILE` binding (server.ts:31) → `ev_b90e8f4455cf50b8331c20ddec0f651eb73d839cb671c9d64fe0138d5c8f80ad`
    (`sem-envfile.json`, sha256 `713f0af86e16777dbc6a043d5dd46437bb178d7199bc0087788910b9c32e4e87`),
    reaching lines 37, 38, 50.
  - `chmodSync(ENV_FILE)` (server.ts:37) → `ev_7259032170ebe963cda68583f20ae13079c719df9eda8a276e0adb23234956a1`.
  - `mcp.notification` read (server.ts:779) → `ev_208502d4a8668c8ea9d121481955024920d68500b6e22655049d85743932927d`.
  - literal `notifications/claude/channel` → `ev_d2514479c90794a836ad0d63fa235ff5734c335bb116630ea9ef1ad5bace9221`:
    0 seed matches (string literals inside object arguments are not literal seeds here) - missing coverage.
  No run reported `cleanup_incomplete`.

## Observed behaviour

1. **[O]** One state directory per channel instance: `STATE_DIR = TELEGRAM_STATE_DIR ?? $CLAUDE_CONFIG_DIR or
   ~/.claude` + `/channels/telegram` (server.ts:27-28, `ev_b7566a5a...`). Every other path (access.json,
   approved/, .env, inbox/, bot.pid) is derived from it (server.ts:29-31, 55-56).
2. **[O]** The token lives in `<state>/.env`, not in the MCP config: the server loads `KEY=value` lines into
   `process.env` only where the real environment has no value ("Real env wins"), and first `chmod 600`s the
   file (server.ts:33-42, `ev_b90e8f44...`, `ev_72590321...`). The plugin's `.mcp.json` holds no secret, only
   `${CLAUDE_PLUGIN_ROOT}` (`.mcp.json` sha256 above).
3. **[O]** Missing token = clear stderr message naming the file and the format, then exit 1 (server.ts:44-53,
   `ev_9b9ac075...`).
4. **[O]** The state dir is created `0700`, access.json written `0600` through a tmp file + rename
   (server.ts:62, 211-216); a corrupt access.json is moved aside, not trusted (server.ts:158-176).
5. **[O]** The server refuses to send its own state files (anything under STATE_DIR except inbox/) as
   attachments (server.ts:144-156, reached from STATE_DIR in `ev_b7566a5a...`).
6. **[O]** Channel push: capabilities `experimental['claude/channel']`, plus server `instructions` that tell
   the session the user cannot see the transcript and must be answered through the reply tool; inbound
   messages go out as `mcp.notification({method: 'notifications/claude/channel', params: {content, meta}})`
   with ids in `meta` (server.ts:391-420, 965-985). Seen by reading the code; REA's literal trace missed it
   (`ev_d2514479...`, no-match).
7. **[O]** Only gate-approved senders reach the notification (server.ts:236-290, 913) and the outbound tools
   only target allowlisted chats (server.ts:201-208). Permission relay is opt-in and only declared because
   the sender is authenticated (server.ts:396-403).
8. **[O]** Setup is a skill (`/telegram:configure`) that resolves the same state dir with the same fallback
   chain and writes `<state>/.env` (skills/configure/SKILL.md, sha256 above).
9. **[O]** A stale poller from a crashed session is detected through a PID file and killed only after `ps`
   confirms it is the same program (server.ts:57-78): restarts must not leave two pollers.

## What we take

- Per-user values in one file outside the repo (`factory.env`, gitignored) with "real env wins" and a clear
  error naming the file when a value is missing (1-3). Our token stays in its own file (`TOKEN_FILE`), never in
  generated configs; `install.sh` checks it is mode 600 (`doctor.sh` warns otherwise).
- Generated MCP config carries only paths and non-secret env, like `.mcp.json` (2).
- State directory created 0700, writes via tmp + rename (4): ask-me already does this under flock.
- Instructions text tells the session to answer only through the reply tool (6); our channel instructions are
  generated from a template with the owner's name and kit paths.
- Delivery only from the authenticated own chat; no permission relay (7) - unchanged from the factory channel.
- Setup steps as a skill usable from both tools (8): the kit ships `foreman`, `ask-me` and `safe-push` skills.

## Not covered

- [U] Runtime behaviour: everything above is static reading; REA did not run the server.
- [U] REA application-graph traces matched nothing (5 no-match results); the semantic traces are `partial`.
  The channel notification path was confirmed by reading server.ts only.
- [U] `bun.lock`, grammy and the MCP SDK internals were not analysed.
- launchd/auto-start (terranc bridge) has no licence and was not REA'd; the kit keeps the foreman in a
  terminal as today.
