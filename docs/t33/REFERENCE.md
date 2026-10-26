# T33 Reference (REA)

Labels: **[O]** observed in code or in a run, **[I]** inferred, **[U]** unknown.

Large evidence files stay outside the repo in `~/factory/evidence/t33/` (`rea/` for REA output, `probe/` for the
probe's scripts and raw hook logs). Only their paths and sha256 digests are recorded here.

## Reference system

- Name and why it is the best real case: **OpenHands StuckDetector** (`openhands-sdk`, `conversation/stuck_detector.py`).
  It is on by default in every OpenHands conversation, its source is open, its thresholds are documented and were
  made configurable after real false positives (OpenHands SDK issue #762, `~/research/run-budget/sources/gh-openhands-sdk-762-stuck-thresholds.md:83`).
  SPEC "Best real case" 1.
- Pinned: git commit `ce5716a3c0a955a85bb0cc4dcfaa94d5fefcda50` (2026-10-25 22:15:56 +0100, "fix(verify-agent-server): gate fresh runs on health and refresh API recipes (#6192)")
  of `https://github.com/OpenHands/software-agent-sdk`, clone at `/private/tmp/t33-ref/oh-sdk`.
  `git archive --format=tar HEAD | shasum -a 256` = `e0a01117082571da24cb862603ad69a85bb27bb99303cd16edd54c54c4dc99db`.
  REA root sha256 of `openhands-sdk/openhands/sdk/conversation/` = `d44332b07a20e631cc9e6c99508dcb89558518afe92085bc2d9c48b323db7790`.
- File digests (sha256):
  - `openhands-sdk/openhands/sdk/conversation/stuck_detector.py` `3bf25f2d722404398e4744ae188f575f628463732b3606a8da6049fb8ff0039e` (370 lines; byte-identical to the research copy `sources/openhands-sdk-stuck_detector-py.md`)
  - `openhands-sdk/openhands/sdk/conversation/types.py` `d5e955b491e9decc987595d4a61803368c2f7fdf9b6cceded60f753a5b6d34ef`
  - `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py` `bd3ba6aaa53b9583fc1f2f19da5034aa122190ebaa970ee8a3e41ae93b5ac1b6`
  - `LICENSE` `14a9b631c658eee682c6c2973525fbdf808c3457176bc47513052c559cc5ce86`
- Licence: MIT ("Copyright (c) 2026 OpenHands contributors"). Reuse: **learn only, no code vendored.** Their detector
  works on typed in-memory event objects (`ActionEvent`, `ObservationEvent`, pydantic models); ours works on Claude Code
  hook payloads and hashes, so no line of theirs fits. README.md credits the design.

## REA runs

`npx -y rea-agents@6.7.0 <command> --help` and `--schema` were read first. All output is in `~/factory/evidence/t33/rea/`.

- `import-reference-source oh-sdk/openhands-sdk/openhands/sdk/conversation --format json --full-output` →
  `import-conversation.json` (sha256 `d70b5adc371d483b4eb1b8adc1bab7c9c7f21cdd0878d19fbe32f34fae057c7c`).
  HistoricalSourceGraph, authority `historical-reference`, root `d44332b0…`, vcs head `ce5716a…`. Result: **limitation**:
  `inventory_state: partial`, `relationships: []` (Python imports are not graphed), files are hashed but not parsed,
  and REA's own note: "a syscall-boundary pathname race remains". This command emits no `ev_` ID.
- `analyze oh-sdk/openhands-sdk/openhands/sdk/conversation --format json --full-output` → evidence
  `ev_9c0f6c7c853eec2b90acd42a8891cad52ff7678236dd4261f27de427ee54326b`, `analyze-conversation.json`
  (sha256 `3fde3c7ef67deeb5ea6a7ccf4a063d2192ad209d24b40cedfa533978e21ca2f6`), subject digest
  `8efce65aa3483ec1613fdac8d9903e504ddea35c8f869b548acad2be4cd798e1`. Result: **limitation**: REA chose its JavaScript
  application analyzer, which selected 0 files; Python is not covered, so the graph has only the root node.
- `trace oh-sdk/…/stuck_detector.py _is_stuck_repeating_action_observation` → `trace-repeat.json`
  (sha256 `1dd42356a74ca92a5bdb0b261395cc3846d68f827ea1b2ffb83b87e0ae5f4261`). Result: refused,
  `target_unavailable` / `unsupported binary format` (a `.py` file is not an artifact REA can open).
- `inspect-artifact oh-sdk/…/stuck_detector.py` → `inspect-stuck.json`
  (sha256 `4b5dc80f1f9623e3cacd82a74569d164578360cf2bea9512dabba5f66d8c8e56`). Result: refused, same reason.

So REA pinned and digested the tree, but none of its analyzers reads Python. The observations below come from reading
the pinned files directly (line numbers at commit `ce5716a`). "No relationship" in REA output is missing coverage, not
proof that a call does not exist.

## Observed behaviour (OpenHands StuckDetector)

1. **[O]** Defaults: `action_observation = 4`, `action_error = 3`, `monologue = 3`, `alternating_pattern = 6`, all
   `ge=1` and configurable (`types.py` l.150-161). Detection is on by default (`conversation.py` l.76
   `stuck_detection: bool = True`). REA digest of `types.py` above; `ev_9c0f6c7c…` covers the tree only.
2. **[O]** It looks only at the last 20 events (`MAX_EVENTS_TO_SCAN_FOR_STUCK_DETECTION = 20`, l.21) and only after
   the last user message (l.66-83). A new user message resets every pattern.
3. **[O]** Same action + same observation: stuck when the last 4 actions are all equal **and** the last 4 observations
   are all equal (l.156-190). A changed observation breaks it. This is the rule SPEC R4 copies ("identical input and
   identical result"); our block at the 3rd and stop at the 6th come from OpenRouter, not from here.
4. **[O]** Equality ignores ids: actions compare `source`, `thought`, `action` and `tool_name`, ignoring
   `tool_call_id`, `llm_response_id`, `action_id` (l.325-341). Observations compare `source`, `observation` and
   `tool_name`. We copy this by hashing only the tool name, the tool input and the result text, never ids.
5. **[O]** Same action + error: the trailing streak of one action whose observation is an `AgentErrorEvent`
   (l.192-206). At exactly 3 it sends **one nudge** message ("Repeating the exact same call again will not work",
   l.218-247, once per streak, l.240); only a streak **above** 3 (the 4th) sets `STUCK` (l.213). Our SPEC stops at the
   3rd (OpenClaw's rule); this is a deliberate difference written in the SPEC, not a change.
6. **[O]** When stuck, the run loop sets `ConversationExecutionStatus.STUCK` and stops stepping
   (`local_conversation.py` l.767-790). Nothing is deleted; the conversation state stays. We copy "stop, keep state"
   (SPEC R3).
7. **[O]** Monologue (3 agent messages with no action) and alternating A/B patterns (6) are also detected
   (l.249-323); the context-window loop check is a stub that returns `False` (l.315-323, TODO issue #282).
8. **[I]** OpenHands has no polling exemption in code; the #762 fix was only to make thresholds configurable.
   Our polling rule (SPEC R4) is our own: a changed result resets, and a bare `sleep` never counts.

## Probe: this Claude Code version (2.1.310), M0 (b)

`claude -p` is blocked on this Mac by his guard (`security-bash.sh`: "headless 'claude -p' is disabled (Anthropic
programmatic credit guard)"). The guard's message offers an override, but the factory does not bypass his cost guard on
its own. Instead the probe drove an **interactive** session (what the guard asks for) in the scratch project
`/private/tmp/t33-probe` with `expect`, model haiku, its own `.claude/settings.json` (a logging hook on every event)
and two agent types. His live settings were not touched. Scripts and raw logs: `~/factory/evidence/t33/probe/`.
Sanitized copies are the test fixtures in `tests/fixtures/run_budget/probe/` (home path rewritten to `/Users/you`).

Raw logs (sha256): `log/p1-run2/events.jsonl` `0be547ad25f8fdbe6afc5bd21ef5cd823d963d796127588bec15f56144999ff6` (deny),
`log/p2-run2/events.jsonl` `abb835b5cb71cd51709de5c0412f87fc15780e15847fc12064afb54dbe31f9dd` (`continue: false`),
`log/p3-run2/events.jsonl` `6931ab9e1d4ca402bf24fd8d3510615b7564fc9b21bb35118540cbc450cf87a1` (`maxTurns: 2`),
`log/p4-run2/events.jsonl` `eb0d2ae0fa3dd7785c535429edc2f53c69af17c249abfebd016744588f759756` (Workflow).

1. **[O] PreToolUse and PostToolUse fire inside background Agent subagents, with `agent_id` and `agent_type`.**
   Every Bash call of the background `probe-worker` arrived with `agent_id: a7b5cd41c23259814`,
   `agent_type: probe-worker`; the foreman's own calls (the `Agent` call) arrive without `agent_id`.
   Fixture `events-deny.jsonl`. They also fire inside **Workflow** subagents (`agent_type: workflow-subagent`,
   fixture `events-workflow.jsonl`).
2. **[O] `transcript_path` in a subagent's hook payload is the *foreman's* transcript**, not the subagent's. The
   subagent transcript is `{transcript_path without .jsonl}/subagents/agent-{agent_id}.jsonl` for Agent runs, and
   `…/subagents/workflows/<wf_id>/agent-<agent_id>.jsonl` for Workflow runs. It exists from the first model request on
   (171 KB at the first Bash call). SubagentStop also carries `agent_transcript_path`. So SPEC R1's "path from
   `transcript_path`" is a derivation, which the code does.
3. **[O] A PreToolUse deny lets the subagent continue.** `echo PROBE_DENY` was denied, no PostToolUse fired for it,
   the run went on to `echo step3` and handed back a report naming the deny. The transcript row carries
   `permissionDecision: {decision: reject, source: hook}`. The same holds in a Workflow subagent.
4. **[O] `continue: false` from a subagent's PreToolUse hook stops only that subagent, and not at once.** The tool
   call it answered still ran (`stdout: PROBE_STOP`), and so did the other call of the same model response
   (`echo after`). Then the subagent ended before its next model request (`hook_stopped_continuation` attachment in its
   transcript), with no SubagentHandback and **no SubagentStop event**. The foreman session went on normally; its
   notification said `completed` and "ended without delivering a report". So the stop stage must pair
   `continue: false` with a deny, and the cost row of a killed run must be written by the hook that kills it.
5. **[O] `maxTurns` is honoured in this version.** `probe-turns` (`maxTurns: 2`) ran `echo t1` and `echo t2` and
   stopped; the notification said "stopped at its 2-turn limit (partial result; SendMessage to task-id to continue)".
   **No SubagentStop fired** for it either, so the cost row comes from the sweep (foreman Stop/PostToolUse reads the
   notification). Issues 41143 and 79303 remain reasons not to rely on it alone (SPEC R9).
6. **[O] K2 holds on all three probe runs**: notification `subagent_tokens` equals the last request's total in the
   subagent transcript (34241, 31449, 33764) and `tool_uses` equals its tool_use blocks (4, 3, 2), with the denied call
   counted. Fixtures `probe/agent-*.jsonl`, `probe/main-session.jsonl`.
7. **[O]** Each session also runs short helper subagents with an empty `agent_type` (no tool calls) that fire
   SubagentStop only. They are not background runs and get no cost row.
8. **[O]** Notifications are stored in the parent's transcript inside `queue-operation`, `attachment` and
   `api-request-blob` rows, more than once each; a subagent's own children notify into the subagent's transcript.
   The parser reads the `<task-notification>` text wherever it is and de-duplicates by task id.
9. **[I]** A nested session (started from inside Claude Code, with `CLAUDE_CODE_CHILD_SESSION` set) wrote no
   transcripts at all in the first probe run; the rerun with those variables unset did. The live foreman is not
   nested, so this only matters for tests.
10. **[U]** Whether a PreToolUse hook's `additionalContext` (the warn stage) is shown to a subagent was not probed; the
    docs say it is added "alongside the tool result" (`claudecode-docs-hooks.md:1963`). The live check covers block
    and stop, which do not depend on it.

## Live check (M4), same scratch setup

`/private/tmp/t33-live` with `run_budget.py` registered as in `settings-hooks.json` (state in a scratch
`RUN_BUDGET_DIR`), a `factory-builder` subagent told to write `notes.txt` and run `echo x` ten times. Raw logs and all
attempts: `~/factory/evidence/t33/live/`; replay test `tests/test_run_budget_live_check.py`.

1. **[O]** Attempt 4 (final code): calls 1-2 ran, the 3rd identical `echo x` was denied, the run committed
   `notes.txt` in its worktree (`git -C <wt> add -A && git -C <wt> commit ...`, allowed), handed back
   "STOPPED: ...", and its row says `stall:repeat`, worktree and last commit filled; `run-budget report T99` printed
   `Cost: 1 run, ctx max 36k, total 206k tok, 6 tools, 0 min, stops: stall:repeat`. Its notification said
   `subagent_tokens 36252`, `tool_uses 6`, equal to the row (K2).
2. **[O]** Attempt 2 found a dodge: after the deny the model appended `# call 3 of 10` to each command, which made
   every input "different". Shell comments and spacing are now ignored when comparing Bash calls.
3. **[O]** Attempts 1 and 3: a run that hands back right after a repeat deny was logged `completed`; and a commit
   with a multi-line message (`-m "...\n\nCo-Authored-By: X <x@y>"`) was not recognised as a save-set call. Both
   fixed: such a run is now `stall:repeat`, and the save set is parsed with quotes and shell operators.
4. **[I]** In all four attempts the model stopped at the first deny instead of reaching the 6th identical call, so
   the block and kill stages were exercised live only through the probe (`continue: false`) and in the replay
   tests, not by a stubborn live run.

## What we take

- From OpenHands: equality on content only (tool + input + result, never ids); the identical-result rule; error
  streaks; stop without deleting anything; configurable thresholds (`RB_REPEAT_BLOCK`, `RB_REPEAT_STOP`,
  `RB_ERROR_STOP`).
- From the probe: act on `agent_id`; derive the subagent transcript path; pair `continue: false` with a deny; write a
  killed run's row from the killing hook; sweep notifications for runs that end without SubagentStop (maxTurns);
  skip helper subagents with an empty `agent_type`.

## Not covered

- REA could not parse Python (limitations above), so every code observation is from direct reading at the pinned commit.
- OpenHands' monologue and alternating-pattern rules are not copied (not in SPEC).
- The probe did not test PostToolUseFailure payloads (no tool failed); the error-streak code reads the documented
  `error` field and is tested on synthetic events.
- `claude -p` itself was not run (his guard); the probe used an interactive session instead.
