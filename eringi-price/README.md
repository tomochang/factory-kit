# eringi-price

One message a day (17:00 JST) to your own chat through `ask-me --tell`: the recent エリンギ wholesale price
trend by origin prefecture (産地). Standard library only, Python 3.9+. Spec: `SPEC.md`, `KGI.md`, `ROADMAP.md`;
reference study: `REFERENCE.md`.

Sources (public, read politely: 1 request at a time, 2 s apart, tool User-Agent, each file once per run):

- 東京都中央卸売市場 市場統計情報明細データ (monthly, every origin x 9 markets), back-filled from 2024-01
- 大阪市中央卸売市場 市況情報 (daily 本場/東部): down (503) since 2026-10-23. Until real pages are saved the
  source is reported as 停止中 or 再開、形式確認待ち and no Osaka price is shown.
- 農林水産省 青果物市況情報 date list and 休市情報 (the open-day calendar)
- 東京都中央卸売市場日報 大田 CSV (watch only: エリンギ is inside 「その他野菜」 today)

Raw files, the run log and the canary log stay in `~/.local/share/eringi-price/` (`ERINGI_DATA_DIR`).

```
./eringi-price backfill          # once: Tokyo months from 2024-01
./eringi-price run --dry-run     # print today's message, send nothing
./eringi-price canary            # planted outages / empty / changed header / missing row on line 1?
./eringi-price audit             # KGI and K1-K5 from the run log
./eringi-price install --print   # the LaunchAgent; `install` loads it (your step)
```

Settings (environment): `ERINGI_DATA_DIR`, `ERINGI_ASK_ME` (path to ask-me.py), `ERINGI_ITEM`,
`ERINGI_ITEM_CODE`, `ERINGI_SEND_TIME` (HH:MM), `ERINGI_DEADLINE`, `ERINGI_BACKFILL_FROM`, `ERINGI_PAUSE`
(at least 1 s), `ERINGI_USER_AGENT`, `ERINGI_LAUNCHD_LABEL`.

Tests: `python3 -m pytest -q tests` (fixtures are real files saved 2026-10-25; nothing is fetched or sent).
