"""大阪市中央卸売市場 市況情報 (R2), 本場 and 東部: エリンギ茸, date, market, origin, high, mid and low (yen/kg).

The site has answered 503 since 2026-10-23 (PHP security stop), so its page format has never been seen. The
parser is NOT written against guessed fields: until real pages are saved (tests/fixtures/osaka/pages/, at least
3 open days and 1 休市 day), a page that answers 200 is kept raw and reported as "再開、形式確認待ち"
(state AWAITING_FORMAT), and no Osaka price is ever shown."""
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from . import calendar as cal
from .sources import (AWAITING_FORMAT, CLOSED, FAILED, NOT_YET, AwaitingFormat, SourceResult, fetch_reason,
                      raw_of)

URL = "https://www.shijou.city.osaka.jp/sikyomap/sikyo"
MARKETS = ("本場", "東部")
ITEM = "エリンギ茸"


@dataclass
class Row:
    date: date
    market: str
    origin: str
    high: Optional[int]
    mid: Optional[int]
    low: Optional[int]
    volume: Optional[float] = None


def parse_osaka(data, day) -> List[Row]:
    raise AwaitingFormat("Osaka 市況情報 format not recorded yet (site 503 since 2026-10-23)")


def check(r, day_status):
    """State of the Osaka source from one fetch of its entry page."""
    raw = raw_of(r)
    if r.status is not None and r.status >= 500:
        return SourceResult("osaka", FAILED, "停止中(%d)" % r.status, raw=raw)
    if r.status == 404:
        if day_status == cal.CLOSED:
            return SourceResult("osaka", CLOSED, raw=raw)
        return SourceResult("osaka", FAILED, "取得失敗(404)", raw=raw)
    if not r.ok:
        return SourceResult("osaka", FAILED, fetch_reason(r), raw=raw)
    if not r.body.strip():
        return SourceResult("osaka", FAILED, "取得失敗(空ファイル)", raw=raw)
    if day_status == cal.CLOSED:
        return SourceResult("osaka", CLOSED, raw=raw)
    try:
        parse_osaka(r.body, None)
    except AwaitingFormat:
        return SourceResult("osaka", AWAITING_FORMAT, AwaitingFormat.reason, raw=raw)
    return SourceResult("osaka", NOT_YET, raw=raw)  # pragma: no cover - reached once a parser exists
