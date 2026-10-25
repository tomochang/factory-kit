# T30 Spec: eringi-price, a daily report of エリンギ wholesale prices by prefecture

Research: `~/research/eringi-price/` (map.md, log.md, report.html, sources/). File names in the "Copied from" column are in its `sources/`.

## Request

「最近のエリンギの値段の推移を、と都道府県別に毎日報告してほしい」 (WhatsApp, 2026-10-25)

Read as: every day, send him the recent trend of エリンギ prices, split by prefecture. The report goes only to his own WhatsApp through `ask-me.py --tell`.

## What the data allows

Checked on 2026-10-25 by opening each source (log.md):

| Source | エリンギ? | How often | Prefecture split | Format | Terms |
|--------|-----------|-----------|------------------|--------|-------|
| 大阪市中央卸売市場 市況情報 (本場, 東部) | yes, 「エリンギ茸」, main origins with high, mid and low price | each market day | origin (主産地: 新潟, 長野) | not seen: the site has returned 503 since 2026-10-23 (PHP security stop) | Osaka city: reuse with 出典 (`osaka-city-site-data-use.md:113,165`) |
| 東京都中央卸売市場 市場統計情報明細データ | yes, 「エリンギだけ」 code 386300 | monthly, about the 15th–20th of the next month | origin × each of 9 Tokyo markets | xlsx | Tokyo: private use and quotation only (`tokyo-shijou-policy.md:123`) |
| 東京都中央卸売市場日報 (販売結果 CSV) | no: inside 「その他野菜」 with no origin or price (`tokyo-nippo-sei_k2-ota-2026-10-23.csv:206`) | each market day | origin for listed items | CSV, CP932 | as above |
| 農林水産省 青果物市況情報 (30 markets) | no: 0 エリンギ rows in all 30 vegetable CSVs on 2026-10-23 (log.md) | each market day, 14:35 | origin | CSV | PDL1.0 (`maff-link-copyright.md:467`) |
| 農林水産省 青果物卸売市場調査 (日別, 旬別) | no: not among the 48 items (`maff-seika-orosi-gaiyou.md:514`) | daily, 旬 | origin | CSV, PDF | PDL1.0 |
| 総務省 小売物価統計 / CPI | no: only 生しいたけ, えのきたけ, しめじ (`stat-cpi2025-item-list.txt:246-248`) | monthly | city | e-Stat | CC BY compatible |
| 林野庁 特用林産物生産統計 | production only, no daily price | yearly | production prefecture | PDF | PDL1.0 |
| 日本農業新聞 netアグリ市況 | probably (not checked) | daily | — | web, login | paid, needs subscription (`nihon-nogyo-agrishikyo.md:61`) |
| GD Freak | yes, made from Osaka and Tokyo data | daily and monthly | origin | web pages | commercial site; not used as a source |

**Chosen meaning of 都道府県別 (default):** the origin prefecture (産地) of the エリンギ, at two markets in two prefectures:

- daily at 大阪 (大阪府) 本場 and 東部 for the main origins listed that day (usually 新潟 and 長野)
- monthly at the 9 東京 (東京都) markets for every origin with volume (2026-09: 長野, 新潟, 香川, 広島, 鳥取, 北海道, 静岡, 福岡, 福島, 群馬)

Market location is shown as the market name only. No free source gives a daily エリンギ price for each of the 47 prefectures. Production by prefecture (林野庁, yearly) is not a price, so it is not in the daily report.

## Best real case

- **Report shape:** GD Freak publishes an automatic daily comment for 大阪本場 エリンギ茸. It gives the main origin of the day, that origin's mid price, this week's average high price against last week's, and the gap to the same period last year (`gdfreak-osaka-honjo-eringi-sanchi.md:30`). Its monthly page for 大田 gives the month's average price, the change from the previous month and the change from the same month last year (`gdfreak-ota-eringi-monthly.md:27`). It is a closed web service, so REA cannot read it. We copy only the shape.
- **Who uses the numbers:**
  - ホクト, the largest grower, puts the Tokyo market monthly エリンギ price in its results briefing. For FY2024 it was 491–750 yen/kg, with an average of 633 (`hokuto-2025-kessan.txt:567,586`). It explains its own price rise by the vegetable market (`hokuto-2025-kessan.txt:147`).
  - 林野庁 uses the Tokyo market yearly price (R7: 599 yen/kg) and production by prefecture (`rinya-kinoko-kentokai-4.txt:415,422-428`).
- **Collector with readable source:** harenoke/budou-shijou (commit `b3f65ae56b5472b445aa105fca2c2c85c3b6aba2`). It is a daily GitHub Actions job that reads the Tokyo 日報 CSVs for all 9 markets, keeps origin, high, mid and low prices, and converts them to yen/kg (`harenoke-budou-shijou.md:127-133`). It treats 404 as "closed" and retries other errors, so that an error is not counted as a holiday (`harenoke-budou-shijou-collect.py.txt:67-69`). It has no licence, so we learn from it only.

## Requirements

| # | Requirement | Serves (KGI/KPI) | Copied from (real case) |
|---|-------------|------------------|-------------------------|
| R1 | **Tokyo monthly source.** Find the newest 青果 「市場統計情報明細データ(xlsx)」 link on the 月報・年報 page by its label and its `y` prefix, not by a fixed file name. Download it, keep the raw file with its sha256, and read the rows whose 品目名称 contains 「エリンギ」 (code 386300). Per origin and per market, keep kg and yen. Price = yen ÷ kg, rounded to whole yen (tax included, as published). Back-fill 2024-01 onward once, so last-year comparisons work from day 1. | KGI, K1, K3 | Tokyo 明細 layout (`tokyo-shijou-meisai-annai-2025.txt:45-51`); file names change with the publish date (`tokyo-shijou-geppo-page.md`); ホクト uses the same source (`hokuto-2025-kessan.txt:567`) |
| R2 | **Osaka daily source.** Read the 大阪市中央卸売市場 市況情報 for 本場 and 東部. Keep エリンギ茸 rows: date, market, origin, high, mid and low (yen/kg) and volume where given. The parser is written against the real page once the site is back (M0 records its format). Until then, Osaka is reported as "停止中" (R5). A day where the row exists but has no high or low price keeps what is there and marks the rest "－". | KGI, K1, K2 | GD Freak's data source and its fields (`gdfreak-osaka-honjo-eringi-sanchi.md:30`, `gdfreak-osaka-honjo-eringi-daily.md:28`); the outage notice (`osaka-shijo-maintenance-2026-10-23.md:115-127`) |
| R3 | **Tokyo daily watch.** Each day, read the Tokyo 日報 青果 販売結果 CSV for all markets (from the temporary page, or shijou-nippo when it is back). If an エリンギ row ever appears with an origin and a price, add it as a daily Tokyo line. Today it is only inside 「その他野菜」, so it is not reported. | K3, K5 | `tokyo-nippo-sei_k2-ota-2026-10-23.csv:206`; budou-shijou reads the same CSVs (`harenoke-budou-shijou.md:130`) |
| R4 | **Open-day calendar.** Use the 農林水産省 青果物市況情報 date list (open days only) and its 休市 page to decide whether today was a market day for 大阪本場 and Tokyo. | KGI, K2 | MAFF date list skips 休市 days (log.md: 10-14, 10-18, 10-21 missing); MAFF publishes at 14:35 (`maff-seisen-shikyo-2026-10-23.md:38`) |
| R5 | **Never stale as new.** For each source, separate four states: new data for today; 休市 (not an open day); not yet published; and fetch failed or format changed (503, empty, header changed, エリンギ row missing on an open day). Only "new data" shows prices as today's. Otherwise line 1 says which state, and any price shown is labelled 「前回 M/D」. A 404 is not taken as 休市 unless the calendar agrees. | KGI, K2, K5 | budou-shijou separates 404 from failures (`harenoke-budou-shijou-collect.py.txt:67-69,150`); outages this week (`tokyo-shijou-nippo-2026-10-25.md:5`, `ibaraki-kinoko-index.md:3`) |
| R6 | **Daily message.** One message a day at 17:00 JST (after the 14:35 publication). At most 700 characters and 10 lines, with line 1 "Nothing needed - ...". For each origin it gives the latest price per kg, the change against last week and against last year, and a trend word (上昇, 横ばい or 下落: the 4-week moving average changing by +5% or more, less than ±5%, or −5% or more). Last week = average of the last 5 open days against the 5 before. Last year = the same 5 open days a year earlier, when that history exists. Until a year of Osaka history exists, last year comes from the Tokyo monthly data and is labelled 「東京月次」. The last line names the sources (出典), as Osaka requires. Sample in "Done when". | KGI, K4 | GD Freak's daily and monthly comments (`gdfreak-osaka-honjo-eringi-sanchi.md:30`, `gdfreak-ota-eringi-monthly.md:27`); Osaka 出典 rule (`osaka-city-site-data-use.md:165`) |
| R7 | **Polite fetching.** At most one fetch per file per run, with a 2-second pause between requests and a User-Agent that names the tool. Raw files and state are kept under `~/.local/share/eringi-price/` (outside any repo). Nothing is republished; only the figures go to his own chat. | KGI | Tokyo site rules: private use and quotation only (`tokyo-shijou-policy.md:123`); budou-shijou's limited parallel fetch (`harenoke-budou-shijou-collect.py.txt`) |
| R8 | **Audit and canary.** `eringi-price audit` computes the KGI and K1–K5 from the run log and the stored raw files. `eringi-price canary` runs the parser on planted bad fixtures (503, empty, changed header, no エリンギ row) and checks that line 1 reports them. | KGI, K1–K5 | KGI measurement; T28 audit and canary pattern |
| R9 | **Delivery as in T22 and T28.** Generic code goes in factory-kit under `eringi-price/`, with tests and settings in `factory.env` (item name, markets, send time); no personal data. His LaunchAgent plist and values go in a clawd PR from a clone under /private/tmp (branch `factory/t30-eringi-price`). No new GitHub repos, nothing merged by the factory, and `~/clawd` is never edited. Installing the LaunchAgent is his step. | all | TASKS.md T22 (kit #4, clawd #160) and T28 |

## Done when

- [ ] Golden tests on the saved real Tokyo files (2025-09, 2026-08 and 2026-09 明細) give 長野 558, 新潟 949 and an all-origin average of 613 yen/kg for 2026-09, and 547 for 2026-08 (+12% month on month). They give a year-on-year change of +9% (613 against 561), and 長野 + 新潟 cover 97.8% of the volume.
- [ ] The Osaka parser passes on real pages saved after the site is back (at least 3 open days and 1 休市 day), and on the planted bad fixtures.
- [ ] State tests: for each of new data, 休市, not yet published, 503, empty, changed header and missing row, line 1 is right and no price is shown as today's unless it is new.
- [ ] Every rendered message, including the longest case (10 Tokyo origins plus 2 Osaka markets), is at most 700 characters and 10 lines, and line 1 starts "Nothing needed - ". A sample from the 2026-09 Tokyo figures plus an Osaka day (Osaka numbers here are made up for the sample):

  ```
  Nothing needed - エリンギ 10/27(火) 大阪本場 新潟1,080円/kg 先週比+8%
  大阪本場(中値) 新潟1,080 先週+8% 前年(東京月次)+31% 上昇
  大阪本場(中値) 長野 691 先週+3% 前年(東京月次)+10% 横ばい
  大阪東部 前回10/24 新潟 972
  東京9市場 9月 平均613 前月+12% 前年+9%
  長野558 新潟949 香川809 広島1,171 鳥取748 他5産地
  出典:大阪市中央卸売市場 市況情報/東京都中央卸売市場 市場統計情報
  ```
- [ ] A 休市 day message, for example "Nothing needed - 10/28(水)は休市。最新は10/27: 大阪本場 新潟1,080円/kg". It has no other new numbers.
- [ ] `eringi-price audit` and `eringi-price canary` run on the fixtures and report the KGI and K1–K5.
- [ ] All existing factory-kit tests pass. The kit PR and the clawd PR are open, and `gh pr checks` shows every CI check passing.

## Out of scope

- Prices for all 47 prefectures. No free daily source has them.
- Retail (shop) prices. The CPI has no エリンギ item.
- 日本農業新聞 netアグリ市況 and other paid sources (an option needing his approval at the Mac, see ROADMAP).
- Scraping GD Freak.
- Price forecasts.
- Sending to anyone but his own chat.
