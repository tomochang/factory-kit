# T22 Spec: WhatsApp replies enforced by code (clawd + factory-kit)

## Request

"you should improve communication with my on whatsApp", "not just say sorry. I want to improve sysytem now", "I can't see our spec. I'm using WhatsApp.", "before message me, check it next time" (WhatsApp, 2026-10-22). He wants the system, not promises, to make the foreman reply on time, short, readable on the phone, with no unchecked "done".

## Best real case

Our own channel (projects/factory/channel in clawd, foreman/channel in factory-kit) and this session's transcript of mistakes: late or missing replies, long multi-part replies, file paths he cannot open on the phone, questions the foreman could decide itself, and "done/ready" sent before CI was checked. Claude Code PreToolUse and Stop hooks are the enforcement point (they run outside the model and can deny a tool call or send a turn back); the Beeper Desktop API on his Mac (v4.3.209, API 5.0.0) supports adding a reaction to a message.

## Requirements

| # | Requirement | Serves (KGI/KPI) | Copied from (real case) |
|---|-------------|------------------|-------------------------|
| R1 | Instant receipt: when the channel reads a new F: message it reacts 👀 to it at once, without the model (fallback: a one-line "✓ got it" marked as from the factory when the reaction call fails). It writes inbox/<id>.json (received, answered=null). After a successful reply it reacts ✅ to every message the reply answers and sets answered. | Receipt KPI; unanswered KPI | Beeper Desktop API POST /v1/chats/{chatID}/messages/{messageID}/reactions |
| R2 | Reply guard: PreToolUse hook on mcp__whatsapp__reply that denies and says exactly how to rewrite when: over REPLY_MAX_CHARS (700) or REPLY_MAX_LINES (10); first line not starting "You:", "Nothing needed" or "Got it"; a local path (~/, /Users/, /home/, /private/, /tmp/) outside a last line starting "File:"; a done/ready/merged/green/passed claim about a PR or CI unless `gh pr checks N --repo R` (timeout REPLY_GH_TIMEOUT) shows every check completed and successful (PR from github.com/.../pull/N URLs or "repo PR #N"/"owner/repo#N", repo owner from REPLY_GH_OWNER; an unresolvable PR or a failed gh call blocks); the 3rd reply within 120 s with no new message from him (REPLY_BURST_MAX, REPLY_BURST_SECONDS). | Length, first-line, path, CI and burst KPIs | his messages; this session's mistakes |
| R3 | Unanswered guard: a Stop hook that, in the foreman session only (FACTORY_SESSION=1, set by foreman.sh), blocks ending the turn while an inbox/<id>.json from this channel run has no answer, naming the message and how to reply. At most 3 blocks per message, then it lets the turn end. Messages left from an earlier channel run are marked stale on start and ignored. | Unanswered KPI | his "replies late or not at all" |
| R4 | The channel instructions get a short "Reply format" section matching R2 (first line, one message, no paths, check CI before claiming green, decide what has a sensible default). | all reply KPIs | his "asks what it could decide itself" |
| R5 | Same behaviour in factory-kit (generic: settings from factory.env, templates) and in clawd projects/factory (his values). Tests for every rule, including a fake gh and a fake Beeper; all existing tests stay green. | all | T15 delivery |
| R6 | Delivery as in T15: factory-kit branch t22-whatsapp-style + PR with all checks green; clawd branch factory/t22-whatsapp-style from a clone under /private/tmp + PR with all checks green. Nothing is merged by the factory; ~/clawd is never edited. | his rule Q18 = A | T15 |

## Done when

- [ ] Reply guard tests: each bad reply class is denied with a rewrite instruction; good replies pass (both repos)
- [ ] Channel tests: 👀 on receipt, ✅ after reply, inbox state written, stale on restart, fallback text when reactions fail
- [ ] Stop hook tests: blocks with an unanswered message, passes after a reply, gives up after 3, ignores non-foreman sessions
- [ ] All existing tests pass; both PRs open with every check green

## Out of scope

- Judging whether a question "could have been decided": instructions only, no hook
- Merging either PR; restarting the live foreman (his step after merge)
