# T30 Reference (REA)

Labels: **[O]** observed in code, **[I]** inferred, **[U]** unknown.

## Reference system

- Name and why it is the best real case: harenoke/budou-shijou. It is a real daily collector of 東京都中央卸売市場 日報 CSVs (all 9 markets, origin, high, mid and low price, converted to yen/kg), run every day by GitHub Actions, with a published dashboard. It is the only readable system found that has to tell "market closed" apart from "fetch failed" for Japanese wholesale-market data (research `~/research/eringi-price/map.md` C3).
- Pinned: git commit `b3f65ae56b5472b445aa105fca2c2c85c3b6aba2` (2026-10-20 05:56:18 +0000, "相場データ更新 2026-10-20"), tree `eb20e1a83c8144d047f1a4f24f2c8d8fa749e495`, `git archive HEAD` tar sha256 `6cedd1d27e3200e870c94a8578ca2a5f1edb03b776f332983da931ef656b666e`. REA root digest `9732130541fb4188dc00b3a7861b929172c1a96a48624afb0cc274f283862965`. Clone: `/private/tmp/t30-budou-shijou-b3f65ae`.
- File digests (sha256): `collect.py` `3830feed6afa21a1d208f145407b7df0882dbc820fb1f8471cbcf8c8f615aea0`, `.github/workflows/collect.yml` `6633297e636e82355f69033e4bee9c1f2cf813674413637a0851ff9ab25816b2`, `README.md` `d62c12ad4f697d997a173b6e4ed75da0af4271495031f03ecacf8ee29dac3528`.
- Licence: none (no LICENSE file in the tree; GitHub API `license: null`, research log.md). Reuse: **learn only**. No code is copied; eringi-price is written from scratch.

## REA runs

Evidence files are outside the repo, in `/private/tmp/t30-rea-evidence/`.

- `npx -y rea-agents@6.7.0 import-reference-source /private/tmp/t30-budou-shijou-b3f65ae --format json --full-output` → `import-full.json` (sha256 `b0a40b8b1c0c810531bb8ad255efca46cf3c3eab72c04dae722211c6e5774c1f`). Result: ok, but a limitation. It is a `HistoricalSourceGraph` (authority `historical-reference`) with `inventory_state: partial`, file hashes for all 11 files, `relationships: []` and no evidence ID. `collect.py` is classified `source`/Python and is only hashed, not parsed. VCS head `b3f65ae…` and `dirty: null`.
- `npx -y rea-agents@6.7.0 analyze /private/tmp/t30-budou-shijou-b3f65ae --format json --full-output` → evidence `ev_09bfb3a4e4a2f7a568acf6f1127da0b7dc5a8d34ca211f468f0d0a52bc9e91e9`, `analyze-root.json` (sha256 `0ec2dc8fb2de4c68f1ae70c66b12039d5111604e1f6a4f3c911c2728efc7cd61`). Result: limitation. REA ran its JavaScript application analysis over 4 files (HTML/JSON); `site/index.html` and `template.html` have inline scripts it did not analyse; Python is not covered.
- `npx -y rea-agents@6.7.0 analyze … --source-path collect.py` → `analyze-collect.json` (sha256 `0bdaf5b7fbb1eeb749f8bf5c4ccd09207901743e806f9d7b2a6fdfad6ba050cc`). Result: refused (`invalid_value`: collect.py is not in its JavaScript inventory).
- `npx -y rea-agents@6.7.0 inspect-artifact …/collect.py` → `inspect-collect.json` (sha256 `4c0d90643d3e74b20bc97de142a85337ef17af16e040e28358039b399911199d`). Result: refused (`unsupported binary format`).
- `npx -y rea-agents@6.7.0 trace-application-feature trace-in-404.json` (seed string `404`) → evidence `ev_101a4a97a5cc3b2f7b4d71cd240462b9192b7fcc95ebf705eefe115c8c1bdd7c`, `trace-out-404.json` (sha256 `d0cfd185fea234ff07e2aa90472b7b1c3a6cd081fbca5ddc6f7cec30126ba060`). Result: `no-match`, source graph `partial`.
- Same with seed `休市` → evidence `ev_0c25d9d93b60e418d63cfd2084d9eae06d9f7adfdfd14eef8b5cbc667f2df2ee`, `trace-out-kyushi.json` (sha256 `64efef4a41dd39e4848231d8c68b3f8a139958119d54758841f763db7a5fe92b`). Result: `no-match`.
- Same with seed `collect.py` → evidence `ev_d729a0f270de66507fed44f7c03730f91e7697fd2b99f2a1e6c68f86c7183c62`, `trace-out-collectpy.json` (sha256 `baf358f0cc7d9e7840ba0ae082cf509873d098bacf242ad6365a89bba2fa92bc`). Result: `no-match`.

REA's "no-match" here is missing coverage (REA 6.7.0 has no Python tracer), not proof of absence. So the behaviour below comes from reading `collect.py` at the pinned commit directly. Each claim gives the line, and the file digest above binds it to `ev_09bfb3a4…`'s root (same tree, root digest `97321305…`).

## Observed behaviour

How it tells "market closed" from "error" (the feature asked for):

1. **[O]** `fetch()` treats HTTP 404 as "closed or not posted, final" and returns `None` at once, without retrying (`collect.py` lines 66-82: `if e.code == 404: return None  # 休市・未掲載（確定）`).
2. **[O]** Any other failure (non-200 status, other HTTP errors, any exception such as a timeout) is retried `RETRIES` times (default 4) with a growing pause of `1.0 * (attempt + 1) + random()` seconds. If all fail, the URL and last reason go into a global `FAILURES` list, and the function also returns `None` (lines 63, 73-88).
3. **[O]** The docstring states why: returning `None` silently would make a network error look like a closed day and leave a hole in the data, so failures are recorded apart (lines 67-70).
4. **[O]** `collect()` counts pages fetched, "休市・未掲載" pages (misses minus failures) and failed pages separately and prints all three (lines 138-148).
5. **[O]** If 0 pages were fetched, it stops with "サイト構成が変わった可能性があります" (site layout may have changed) (lines 149-150).
6. **[O]** If failures exceed 5% of the requests, it stops and asks to lower the worker count and retry later (lines 156-158).
7. **[O]** A row whose high, mid and low are all "－" (full-width dash) is dropped; "－" means "no value" (lines 33, 118-119). Partial dashes are kept and become `None` in `num()` (lines 190-199).
8. **[O]** The market name comes from line 1 of each CSV (`販売結果（青果・大田）` → `大田`); if it is missing the file yields no rows (lines 91-105). That is a weak header check.
9. **[O]** Price per kg = price ÷ 量目 (unit kg), rounded with Python `round()` (line 219).
10. **[O]** Requests carry a tool User-Agent `budou-shijou-collector/1.0` and run with at most 4 parallel workers, because the server drops many connections (lines 19, 72, 139).
11. **[O]** The job runs daily at 10:00 JST (`collect.yml` cron `0 1 * * *`) and re-reads the last 12 days each time (`DAYS_BACK`), merging and de-duplicating by key (lines 17, 170-187).
12. **[I]** A 404 on an open market day (for example a late or moved file) is counted as "closed", because nothing checks a market calendar. Our R5 adds that check: a 404 is 休市 only when the MAFF calendar agrees.
13. **[I]** A day where the site answers 200 with an empty body, or a CSV with a different first line, gives 0 rows and is counted as a fetched page, so it is not reported as a failure unless every page does that (item 5). Our K5 reports empty files and changed headers per source on line 1.
14. **[U]** How the job behaved during the 2026-10-23 日報 outage (the old shijou-nippo site stopped). The pinned commit is from 2026-10-20, before it.

## What we take

- Separate "closed" from "failed" explicitly, record failures with their reason, and never let a failed fetch read as a holiday (items 1-4). We go further: four states per source (new, 休市, not yet published, fetch failed/format changed), and 404 counts as 休市 only if the calendar agrees.
- A "nothing came back at all" check as a sign of a site change (item 5), done per source on every run.
- "－" means no value; keep the fields that are there (item 7, and SPEC R2).
- A tool User-Agent and a gentle request rate (item 10). We use 1 request at a time with a 2-second pause and at most one fetch per URL per run.
- Re-reading recent history and de-duplicating by key, so a late file still lands (item 11). Our Tokyo back-fill keeps one file per month by its 年月 and skips months already stored.

## Not covered

- GD Freak (jp.gdfreak.com): **not readable**. It is a closed web service, so there is no code, binary or bundle for REA. We copy only its report shape (daily main-origin comment, week-on-week, year-on-year; research `gdfreak-osaka-honjo-eringi-sanchi.md:30`). REA: not applicable for it.
- 大阪市中央卸売市場 市況情報: the page format is **unknown**. The site has answered HTTP 503 since 2026-10-23, and again on 2026-10-25 at 18:1x JST (saved: `tests/fixtures/osaka/sikyo-503-2026-10-25.html`). The Osaka parser waits for real pages (state `awaiting_format`).
- REA coverage gaps: Python (`collect.py`) is not parsed or traced by REA 6.7.0; the two HTML templates' inline scripts were not analysed. The dashboard (`template.html`) was not studied; it is not needed for a chat report.
