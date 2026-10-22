# T22 Roadmap

| Milestone | What is usable after it | Serves (KGI/KPI) | Test that proves it |
|-----------|-------------------------|------------------|---------------------|
| M1 Kit: channel receipt + state | 👀/✅ reactions, inbox/<id>.json, replies.jsonl | Receipt KPI | foreman/channel/test/channel.test.mjs |
| M2 Kit: reply guard + Stop hook + instructions | Bad replies denied with a rewrite hint; turns cannot end with his message unanswered | Length, first-line, path, CI, burst, unanswered KPIs | tests/test_reply_guard.py, tests/test_unanswered_stop.py |
| M3 Kit PR | PR on factory-kit with all CI checks green | all | GitHub checks |
| M4 clawd branch + PR | Same code in projects/factory with his values; adversarial review if the gate TCB is touched | all | clawd CI suite run locally, then GitHub checks |
| M5 Activation (his step) | Merge, pull ~/clawd, restart foreman.sh | KGI | replies.jsonl scan after a week |

## Risks

- WhatsApp allows one reaction per person per message, so ✅ replaces 👀 instead of adding to it (that is the intended "seen → answered" change).
- The Stop hook applies to every session in ~/factory; it is limited to the foreman by FACTORY_SESSION=1 and capped at 3 blocks per message so a dead Beeper cannot trap a session.
- gh can be slow or logged out: the guard then blocks the claim and tells the foreman to say "checks not verified" instead.
- Text rules can be gamed by a model that wants to; they stop accidental bad replies, not a determined one.
