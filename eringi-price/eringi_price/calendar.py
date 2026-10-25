"""Open-day calendar (R4) from the 農林水産省 青果物市況情報 pages: the date list shows open days only (published
14:35 on each open day), and the 休市情報 popup carries the 休市 days (some or all markets) for about 4 months,
including future ones (hidden input "kyuichibi")."""
import re
from datetime import date, datetime, time

from .sources import Empty, FormatChanged

DATES_URL = "https://www.seisen.maff.go.jp/seisen/bs04b040md001/BS04B040UC010SC999-Evt001.do"
CLOSED_URL = "https://www.seisen.maff.go.jp/seisen/bs04b040md001/BS04B040UC010SC001-Evt002.do"
PUBLISH = time(14, 35)

OPEN = "open"
PARTIAL = "partial"   # in both lists: some markets closed (e.g. a 祝日), others open
CLOSED = "closed"
NOT_YET = "not_yet"
UNKNOWN = "unknown"   # calendar could not be read


def _d(s):
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def parse_open_dates(page):
    if not page or not page.strip():
        raise Empty()
    found = re.findall(r'name="s006\.dataDate"\s+value="(\d{8})"', page)
    if not found:
        raise FormatChanged("no dates in the MAFF date list")
    return {_d(s) for s in found}


def parse_closed_dates(page):
    if not page or not page.strip():
        raise Empty()
    m = re.search(r'name="kyuichibi"\s+value="([\d,]*)"', page)
    if not m:
        raise FormatChanged("no kyuichibi in the MAFF 休市 page")
    return {_d(s) for s in m.group(1).split(",") if len(s) == 8}


class Calendar:
    def __init__(self, open_dates, closed_dates=()):
        self.open_dates = set(open_dates)
        self.closed_dates = set(closed_dates)

    def status(self, day, now=None):
        if day in self.open_dates:
            return PARTIAL if day in self.closed_dates else OPEN
        if day in self.closed_dates:
            return CLOSED
        return NOT_YET

    def last_open(self, on_or_before):
        days = [d for d in self.open_dates if d <= on_or_before]
        return max(days) if days else None


def published(now: datetime):
    return now.time() >= PUBLISH
