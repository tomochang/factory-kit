"""R2 Osaka daily source. The 市況情報 site has answered 503 since 2026-10-23 (real body saved 2026-10-25), so the
page format is unknown. Until real pages are saved, Osaka is either 停止中 or, once it answers, "awaiting
format": its raw page is kept so the format can be recorded, and no price is ever shown from it."""
from datetime import date

import pytest
from conftest import FIX, fetcher_for, real

from eringi_price import calendar as cal
from eringi_price import osaka
from eringi_price.sources import AWAITING_FORMAT, CLOSED, FAILED, AwaitingFormat


def check(config, status, body, day_status):
    f, _ = fetcher_for(config, {osaka.URL: (status, body)})
    return osaka.check(f.get(osaka.URL), day_status)


def test_real_503_is_a_stop_not_a_holiday(config):
    r = check(config, 503, real("osaka/sikyo-503-2026-10-25.html"), cal.OPEN)
    assert r.state == FAILED and r.reason == "停止中(503)"
    assert r.data is None


def test_503_on_a_closed_day_still_reports_the_stop(config):
    r = check(config, 503, b"", cal.CLOSED)
    assert r.state == FAILED and r.reason == "停止中(503)"


def test_404_is_holiday_only_when_the_calendar_agrees(config):
    assert check(config, 404, b"", cal.CLOSED).state == CLOSED
    r = check(config, 404, b"", cal.OPEN)
    assert r.state == FAILED and r.reason == "取得失敗(404)"


def test_site_back_means_awaiting_format_and_raw_is_kept(config):
    r = check(config, 200, "<html>市況情報 エリンギ茸</html>".encode(), cal.OPEN)
    assert r.state == AWAITING_FORMAT
    assert r.data is None
    assert r.raw and r.raw[0]["sha256"] and (config.data_dir / "raw").exists()


def test_empty_200_is_a_failure(config):
    r = check(config, 200, b"", cal.OPEN)
    assert r.state == FAILED and r.reason == "取得失敗(空ファイル)"


def test_parser_refuses_until_the_format_is_recorded():
    with pytest.raises(AwaitingFormat):
        osaka.parse_osaka(b"<html></html>", date(2026, 10, 26))


REAL_PAGES = sorted((FIX / "osaka" / "pages").glob("*")) if (FIX / "osaka" / "pages").exists() else []


@pytest.mark.skipif(len(REAL_PAGES) < 4, reason="awaiting real Osaka pages (site 503 since 2026-10-23): "
                    "SPEC needs at least 3 open days and 1 休市 day saved in tests/fixtures/osaka/pages/")
def test_parser_on_real_pages():  # pragma: no cover - runs once real pages are saved
    for p in REAL_PAGES:
        rows = osaka.parse_osaka(p.read_bytes(), date.fromisoformat(p.stem[:10]))
        assert all(r.market in osaka.MARKETS for r in rows)
