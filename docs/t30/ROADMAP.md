# T30 Roadmap

| Milestone | What is usable after it | Serves (KGI/KPI) | Test that proves it |
|-----------|-------------------------|------------------|---------------------|
| M0 REA and source check | REFERENCE.md from harenoke/budou-shijou, pinned at commit `b3f65ae56b5472b445aa105fca2c2c85c3b6aba2` (no licence, so learn only). It covers how it finds the files, treats 404, retries and spots a changed site. The Osaka 市況情報 format is recorded once the site is back (saved real pages); GD Freak is "REA: not applicable" (closed web service) | K2, K5 | `check_task.py`; REFERENCE claims carry `ev_` IDs |
| M1 Tokyo monthly | `eringi-price run --dry-run` prints the Tokyo lines by origin, with the change against last month and last year, back-filled from 2024-01 (R1) | KGI, K1, K3 | `tests/test_tokyo_monthly.py` (golden: 2026-09 長野 558, 新潟 949, all 613 yen/kg, +9% year on year, 97.8% coverage) |
| M2 Calendar and states | Open-day calendar from MAFF (R4) and the four source states (R5). Outage and 休市 messages work even while Osaka is down | KGI, K2, K5 | `tests/test_states.py`, `tests/test_calendar.py` |
| M3 Osaka daily | Osaka 本場 and 東部 parser (R2) and the Tokyo daily watch (R3), built on real saved pages after the site is back | KGI, K1 | `tests/test_osaka.py` (at least 3 open days and 1 休市 day), `tests/test_tokyo_daily.py` |
| M4 Message, audit, canary | The daily 17:00 JST message to his own chat through ask-me (R6), polite fetching (R7), audit and canary (R8) | KGI, K1–K5 | `tests/test_message.py` (700 characters and 10 lines, line 1, sample), `tests/test_audit.py`, `tests/test_canary.py` |
| M5 Delivery | factory-kit PR plus clawd PR (branch `factory/t30-eringi-price`) with every CI check passing (R9) | all | GitHub checks via `gh pr checks` |
| M6 His step and trial | He merges and installs the LaunchAgent. A 28-day trial follows, with a weekly canary, ending 2026-12-15 | KGI | `eringi-price audit` after 28 days |

## Cost

Free. All sources used are free public pages. It runs on his Mac from a LaunchAgent, so there is no server cost.

Paid option (not in the build; needs his approval at the Mac): 日本農業新聞 netアグリ市況 needs a newspaper subscription and login (`nihon-nogyo-agrishikyo.md:61`). It may carry more markets' daily エリンギ prices (not checked). The WAGRI 青果物市況情報 API needs an application (not checked whether エリンギ is in it or whether an individual can apply).

## Risks

- **Main risk: the only free daily エリンギ source is down.** Since 2026-10-23, the 大阪市 市況情報 site has returned 503 for a PHP security fix. Results are only posted on boards inside the markets (`osaka-shijo-maintenance-2026-10-23.md:115-127`). The page format and the length of its history are unknown until it is back. If it stays down, or comes back without エリンギ, the daily part stops and only the Tokyo monthly report remains. On those days the daily message just says that. Mitigation: M1 and M2 do not depend on Osaka, and M3 waits for real pages. The weekly "source check" line tells him when Osaka returns. A paid source is the fallback, with his approval.
- **"毎日" is partly monthly.** Tokyo, the largest market, publishes エリンギ by origin only monthly, about 2–3 weeks after the month ends (`tokyo-shijou-geppo-page.md:20-24`). Its daily CSV hides エリンギ in 「その他野菜」. The daily message repeats the latest Tokyo month, labelled with its month, until the next one comes out.
- **Few origins daily.** Osaka lists only the main origins of the day (usually 新潟 and 長野). Smaller origins (香川, 広島, 鳥取, 福岡) appear only in the Tokyo monthly data.
- **Site and format changes.** The Tokyo statistics site moved in 2025 and the old one closed (`tokyo-shijou-meisai-annai-2025.txt:43-44`). File names carry the publish date. Mitigation: find links by label, check the header, and report a change on line 1 (K5).
- **Terms.** Tokyo allows private use and quotation only (`tokyo-shijou-policy.md:123`). The report goes only to his own chat, raw files stay on his Mac, and nothing is republished.
- **Missing prices on a day.** Osaka sometimes lists エリンギ with no high or low price (`gdfreak-osaka-honjo-eringi-daily.md:28`). Mitigation: show what is there and mark the rest "－". The day is not treated as a 休市.
