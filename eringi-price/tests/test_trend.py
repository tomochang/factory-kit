"""R6 daily trend rules. No real Osaka series exists yet (site down), so these use made-up prices; the rules are
the SPEC's: last week = mean of the last 5 open days vs the 5 before; last year = the same 5 open days a year
earlier; trend word from the 4-week moving average (20 open days vs the 20 before): >= +5% 上昇,
<= -5% 下落, else 横ばい."""
from datetime import date, timedelta

from eringi_price import trend


def series(prices, start=date(2026, 9, 1)):
    out, d = [], start
    for p in prices:
        while d.weekday() in (2, 6):  # skip Wed and Sun like a market calendar
            d += timedelta(days=1)
        out.append(trend.Point(d, p))
        d += timedelta(days=1)
    return out


def test_week_change():
    s = series([100] * 5 + [108] * 5)
    assert trend.week_change(s) == 8
    assert trend.week_change(series([100] * 9)) is None


def test_year_change_needs_last_year_points():
    last = series([100] * 5, start=date(2025, 10, 1))
    now = series([131] * 5, start=date(2026, 10, 1))
    assert trend.year_change(last + now) == 31
    assert trend.year_change(now) is None


def test_trend_words():
    assert trend.trend_word(series([100] * 20 + [106] * 20)) == "上昇"
    assert trend.trend_word(series([100] * 20 + [103] * 20)) == "横ばい"
    assert trend.trend_word(series([100] * 20 + [95] * 20)) == "下落"
    assert trend.trend_word(series([100] * 15)) is None


def test_none_prices_are_skipped():
    s = series([100] * 5 + [None] + [108] * 5)
    assert trend.week_change(s) == 8
