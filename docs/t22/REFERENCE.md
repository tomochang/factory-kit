# T22 Reference (REA)

Labels: **[O]** observed in code, **[I]** inferred, **[U]** unknown.

REA: not applicable - our own channel code. The system to change is our own WhatsApp channel (clawd projects/factory/channel/whatsapp-channel.mjs and factory-kit foreman/channel/), which we read directly; there is no outside system to reverse-engineer. The research basis is his messages of 2026-10-22 and this session's mistakes (see SPEC.md).

## Reference system

- Name and why it is the best real case: our own channel; it is the code that sends the replies he complains about.
- Pinned: clawd origin/main and factory-kit origin/main f2b43e5 at the time of the branch; Beeper Desktop 4.3.209 (local API spec 5.0.0, read with GET /v1/spec)
- Licence: our own code; reuse: base code

## Observed behaviour

1. **[O]** The channel delivers only messages starting "F:" and never acknowledges them; the only output is the model's reply tool call (whatsapp-channel.mjs, poll and reply handler).
2. **[O]** The reply tool posts any text of any length; there is no hook on mcp__whatsapp__reply in workspace-settings.json (only Bash and Write/Edit guards).
3. **[O]** Beeper Desktop API 5.0.0 offers POST /v1/chats/{chatID}/messages/{messageID}/reactions with body {reactionKey}, returning {success, chatID, messageID, reactionKey, transactionID}.
4. **[I]** WhatsApp keeps one reaction per person per message, so a later ✅ replaces the 👀.

## What we take

- Hooks outside the model as the enforcement point; the channel server as the only writer of inbox and reply state.

## Not covered

- Whether WhatsApp shows the reaction on a "message yourself" chat on every phone client [U]: checked only against the API, not on his phone.
