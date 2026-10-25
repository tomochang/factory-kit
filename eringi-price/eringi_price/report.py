"""One run: read every source once, decide each source's state (R5), and gather what the message shows."""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

from . import calendar as cal
from . import osaka, tokyo_daily, tokyo_monthly, trend
from .sources import (AWAITING_FORMAT, CLOSED, FAILED, NEW, NOT_YET, SourceError, SourceResult, fetch_reason,
                      raw_of)

LABELS = {"calendar": "市場カレンダー", "osaka": "大阪", "tokyo": "東京月次", "tokyo_daily": "東京日報"}


@dataclass
class DailyLine:
    source: str           # "osaka" or "tokyo_daily"
    market: str
    origin: str
    price: Optional[int]  # 中値 yen/kg
    as_of: date
    today: bool
    week: Optional[int] = None
    year: Optional[int] = None
    year_label: Optional[str] = None
    trend: Optional[str] = None


@dataclass
class Report:
    day: date
    now: datetime
    day_status: str
    calendar: SourceResult
    osaka: SourceResult
    tokyo: SourceResult
    tokyo_daily: SourceResult
    tokyo_months: Dict[str, tokyo_monthly.Month] = field(default_factory=dict)
    tokyo_newest: Optional[str] = None
    osaka_lines: List[DailyLine] = field(default_factory=list)
    tokyo_daily_lines: List[DailyLine] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    def sources(self):
        return {"calendar": self.calendar, "osaka": self.osaka, "tokyo": self.tokyo, "tokyo_daily": self.tokyo_daily}


def _fail(source, r, exc=None):
    return SourceResult(source, FAILED, exc.reason if exc else fetch_reason(r), raw=raw_of(r))


def read_calendar(fetcher, day, now):
    r1 = fetcher.get(cal.DATES_URL)
    if not r1.ok:
        return SourceResult("calendar", FAILED, fetch_reason(r1), raw=raw_of(r1)), cal.UNKNOWN
    try:
        open_dates = cal.parse_open_dates(r1.body.decode("utf-8", errors="replace"))
    except SourceError as e:
        return _fail("calendar", r1, e), cal.UNKNOWN
    r2 = fetcher.get(cal.CLOSED_URL)
    closed = set()
    if r2.ok:
        try:
            closed = cal.parse_closed_dates(r2.body.decode("utf-8", errors="replace"))
        except SourceError as e:
            return SourceResult("calendar", FAILED, e.reason, raw=raw_of(r1, r2)), cal.UNKNOWN
    else:
        return SourceResult("calendar", FAILED, fetch_reason(r2), raw=raw_of(r1, r2)), cal.UNKNOWN
    c = cal.Calendar(open_dates, closed)
    status = c.status(day, now)
    state = {cal.CLOSED: CLOSED, cal.NOT_YET: NOT_YET}.get(status, NEW)
    return SourceResult("calendar", state, as_of=day.isoformat(), data=c, raw=raw_of(r1, r2)), status


def update_tokyo(fetcher, store, config, now, want=None):
    """Fetch the 明細 page and any needed month not yet stored. want=None: the newest month plus the month
    before and the same month a year earlier (what the message compares against)."""
    page = fetcher.get(tokyo_monthly.PAGE_URL)
    if not page.ok:
        return _fail("tokyo", page), {}
    try:
        links = tokyo_monthly.find_links(page.body.decode("utf-8", errors="replace"))
    except SourceError as e:
        return _fail("tokyo", page, e), {}
    newest = max(links)
    if want is None:
        want = [newest, tokyo_monthly.prev_ym(newest), tokyo_monthly.prev_ym(newest, 12)]
    raw, got, err = [page], [], None
    for ym in sorted(set(want)):
        if ym not in links or store.has_tokyo(ym):
            continue
        r = fetcher.get(links[ym])
        raw.append(r)
        if not r.ok:
            err = err or (ym, fetch_reason(r))
            continue
        try:
            store.put_tokyo(ym, links[ym], r.body, at=now)
            got.append(ym)
        except SourceError as e:
            err = err or (ym, e.reason)
    if err and err[0] == newest:
        return SourceResult("tokyo", FAILED, err[1], as_of=newest, raw=raw_of(*raw)), links
    state = NEW if newest in got else NOT_YET
    if err:  # an older month failed: still a source problem worth line 1
        return SourceResult("tokyo", FAILED, err[1], as_of=err[0], raw=raw_of(*raw)), links
    return SourceResult("tokyo", state, as_of=newest, raw=raw_of(*raw)), links


def read_tokyo_daily(fetcher, day, config):
    r = fetcher.get(tokyo_daily.URL)
    if not r.ok:
        return _fail("tokyo_daily", r), []
    try:
        n = tokyo_daily.parse_nippo(r.body, item=config.item)
    except SourceError as e:
        return _fail("tokyo_daily", r, e), []
    state = NEW if n.date == day else NOT_YET
    lines = [DailyLine("tokyo_daily", n.market, x.origin, x.mid, n.date, n.date == day) for x in n.rows[:2]]
    return SourceResult("tokyo_daily", state, as_of=n.date.isoformat(), data=n, raw=raw_of(r)), lines


def osaka_lines(rows, day, tokyo_months, per_market=2):
    """Lines for each Osaka market: the main origins of `day` with week, year and trend, or the latest earlier
    day labelled 前回."""
    out = []
    newest_tokyo = max(tokyo_months) if tokyo_months else None
    for market in osaka.MARKETS:
        mrows = [r for r in rows if r.market == market and r.date <= day]
        if not mrows:
            continue
        last = max(r.date for r in mrows)
        todays = sorted((r for r in mrows if r.date == last), key=lambda r: -(r.volume or 0))[:per_market]
        for r in todays:
            pts = [trend.Point(x.date, x.mid) for x in mrows if x.origin == r.origin]
            line = DailyLine("osaka", market, r.origin, r.mid, last, last == day)
            if line.today:
                line.week = trend.week_change(pts)
                line.year = trend.year_change(pts)
                if line.year is None and newest_tokyo:
                    s = tokyo_monthly.summary(tokyo_months, newest_tokyo)
                    cur, ago = s.month.origin(r.origin), s.year_ago.origin(r.origin) if s.year_ago else None
                    if cur and ago:
                        line.year, line.year_label = tokyo_monthly.pct(cur.price, ago.price), "東京月次"
                line.trend = trend.trend_word(pts)
            out.append(line)
    return out


def collect(fetcher, store, config, now):
    day = now.date()
    calendar, status = read_calendar(fetcher, day, now)
    o = osaka.check(fetcher.get(osaka.URL), status)
    tokyo, _ = update_tokyo(fetcher, store, config, now)
    td, td_lines = read_tokyo_daily(fetcher, day, config)
    months = store.tokyo_months()
    rep = Report(day, now, status, calendar, o, tokyo, td, months, max(months) if months else None)
    rep.osaka_lines = osaka_lines(store.osaka_rows(), day, months)
    rep.tokyo_daily_lines = td_lines
    rep.problems = problems(rep)
    return rep


def problems(rep):
    out = []
    if rep.day_status == cal.NOT_YET:
        out.append("本日分は未公表")
    for name in ("osaka", "calendar", "tokyo", "tokyo_daily"):
        s = rep.sources()[name]
        if s.state in (FAILED, AWAITING_FORMAT):
            out.append("%sは%s" % (LABELS[name], s.reason))
    return out
