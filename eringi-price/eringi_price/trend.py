"""Daily trend rules (R6).

- last week: mean of the last 5 open days against the mean of the 5 before (needs 10 priced days)
- last year: mean of the same 5 open days a year earlier (dates shifted by 364 days, +-3 days), when that
  history exists
- trend word: the 4-week moving average (last 20 open days) against the 4 weeks before it (the 20 before):
  +5% or more 上昇, -5% or less 下落, otherwise 横ばい (needs 40 priced days)"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional

from .tokyo_monthly import pct


@dataclass
class Point:
    date: date
    price: Optional[float]


def _priced(points):
    return sorted((p for p in points if p.price is not None), key=lambda p: p.date)


def _mean(ps):
    return sum(p.price for p in ps) / len(ps)


def week_change(points: List[Point]) -> Optional[int]:
    ps = _priced(points)
    if len(ps) < 10:
        return None
    return pct(_mean(ps[-5:]), _mean(ps[-10:-5]))


def year_change(points: List[Point]) -> Optional[int]:
    ps = _priced(points)
    if len(ps) < 5:
        return None
    last = ps[-5:]
    lo, hi = last[0].date - timedelta(days=367), last[-1].date - timedelta(days=361)
    ago = [p for p in ps if lo <= p.date <= hi]
    if not ago:
        return None
    return pct(_mean(last), _mean(ago[-5:]))


def trend_word(points: List[Point]) -> Optional[str]:
    ps = _priced(points)
    if len(ps) < 40:
        return None
    c = pct(_mean(ps[-20:]), _mean(ps[-40:-20]))
    return "上昇" if c >= 5 else "下落" if c <= -5 else "横ばい"
