# T22 KGI

## KGI

- Metric: share of foreman WhatsApp replies that break a reply rule (too long, first line not saying what he must do, a local file path he can't open, a "done/green" claim while the PR's checks are not all green, a burst of replies, a message of his left unanswered at the end of a turn) and actually reach his phone
- Target: 0 rule-breaking replies delivered; every F: message from him gets a visible receipt (👀) within one poll interval (10 s by default) and a ✅ once the foreman has replied
- Deadline: 2026-11-05
- How measured: locally, the hook tests feed each bad reply class to the guard and require a block (tests/test_reply_guard.py in both repos) and the channel tests check the 👀/✅ reactions against a fake Beeper. After delivery: the channel writes every sent reply to replies.jsonl in its state folder; a weekly scan of that log for the same rules must find 0 violations, and every inbox/<id>.json must have answered_at set.
- Based on: his own messages of 2026-10-22 ("you should improve communication with my on whatsApp", "not just say sorry. I want to improve sysytem now", "I can't see our spec. I'm using WhatsApp.", "before message me, check it next time") and this session's mistakes (late or missing replies, multi-part long replies, file paths, questions it could decide, "done" before CI). The thresholds (700 chars, 10 lines, 3 replies in 2 min) are an assumption.

## KPIs

| KPI | Target | How measured | Based on |
|-----|--------|--------------|----------|
| Receipt mark on each F: message | 👀 within 1 poll (10 s), without the model | channel test with fake Beeper (reaction POST seen) | his "replies late or not at all" complaint; Beeper Desktop API 5.0.0 has POST .../messages/{id}/reactions (local /v1/spec, read with GET) |
| Replies over the length/line limit delivered | 0 (limit 700 chars / 10 lines, factory.env) | test_reply_guard | "sends long and multi-part messages"; limits are an assumption |
| Replies whose first line does not start "You:" / "Nothing needed" / "Got it" | 0 | test_reply_guard | "wastes his time": he must see at once whether he has to act |
| Replies with a local path outside a last "File:" line | 0 | test_reply_guard | "I can't see our spec. I'm using WhatsApp." |
| "done/ready/merged/green" claims about a PR whose checks are not all green | 0 | test_reply_guard with a fake gh | "before message me, check it next time" |
| Turns that end with a message of his unanswered | 0 (the Stop hook sends the turn back, at most 3 times per message) | test_unanswered_stop | "replies late or not at all" |
