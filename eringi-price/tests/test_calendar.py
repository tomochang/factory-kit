"""M2 open-day calendar (R4; KGI, K2) from the real MAFF 青果物市況情報 pages saved 2026-10-25."""
from datetime import date

import pytest
from conftest import at, real

from eringi_price import calendar as cal
from eringi_price.sources import Empty, FormatChanged


@pytest.fixture(scope="module")
def c():
    return cal.Calendar(cal.parse_open_dates(real("maff/dates-2026-10-25.html").decode()),
                        cal.parse_closed_dates(real("maff/kyushi-2026-10-25.html").decode()))


def test_open_dates_skip_closed_days(c):
    assert date(2026, 10, 23) in c.open_dates and date(2026, 10, 24) in c.open_dates
    for d in (14, 18, 21):  # research log.md: missing from the MAFF list
        assert date(2026, 10, d) not in c.open_dates
    assert len(c.open_dates) == 10


def test_closed_list_includes_future_days(c):
    assert date(2026, 10, 25) in c.closed_dates and date(2026, 10, 28) in c.closed_dates
    assert date(2026, 11, 29) in c.closed_dates


def test_status(c):
    assert c.status(date(2026, 10, 25), at(2026, 10, 25)) == cal.CLOSED
    assert c.status(date(2026, 10, 23), at(2026, 10, 23)) == cal.OPEN
    # 10/12 is in both lists: some markets closed (祝日), others open
    assert c.status(date(2026, 10, 12), at(2026, 10, 12)) == cal.PARTIAL
    # 10/26 is not in either list yet: before or after 14:35 it is "not yet published"
    assert c.status(date(2026, 10, 26), at(2026, 10, 26, 10, 0)) == cal.NOT_YET
    assert c.status(date(2026, 10, 26), at(2026, 10, 26, 17, 0)) == cal.NOT_YET


def test_last_open(c):
    assert c.last_open(date(2026, 10, 25)) == date(2026, 10, 24)
    assert c.last_open(date(2026, 10, 22)) == date(2026, 10, 22)


def test_changed_or_empty_pages_raise():
    with pytest.raises(FormatChanged):
        cal.parse_open_dates("<html><body>システムメンテナンス</body></html>")
    with pytest.raises(Empty):
        cal.parse_open_dates("")
    with pytest.raises(FormatChanged):
        cal.parse_closed_dates("<html></html>")
