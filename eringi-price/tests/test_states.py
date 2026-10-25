"""M2 the four source states (R5; KGI, K2, K5). Each case runs the whole collect + message pipeline on the real
sources of 2026-10-25 with one source changed. Line 1 must say what happened, and no price may be shown as
today's unless it is new."""
import pytest
from conftest import BASE, at, fetcher_for, real, seed_months, world_2026_10_25, xlsx

from eringi_price import calendar as cal
from eringi_price import message, osaka, report, tokyo_daily, tokyo_monthly
from eringi_price import xlsx as X
from eringi_price.sources import AWAITING_FORMAT, CLOSED, FAILED, NEW, NOT_YET

TOKYO_LINES = ["東京9市場 9月 平均613 前月+12% 前年+9%", "長野558 新潟949 香川809 広島1,171 鳥取748 他5産地"]


def render(config, store, world, now):
    f, _ = fetcher_for(config, world)
    rep = report.collect(f, store, config, now)
    return rep, message.build(rep)


@pytest.fixture
def full(store):
    return seed_months(store, ["202509", "202608", "202609"])


def lines(built):
    return built.text.split("\n")


def assert_no_today_price(built):
    assert "(中値)" not in built.text
    assert not any(f.get("today") for f in built.figures)


def test_real_sunday_is_closed_and_osaka_stop_is_said(config, full):
    rep, b = render(config, full, world_2026_10_25(), at(2026, 10, 25))
    assert rep.day_status == cal.CLOSED
    assert lines(b)[0] == "Nothing needed - 10/25(日)は休市。大阪は停止中(503)。最新は東京9市場 9月 平均613円/kg"
    assert lines(b)[1:3] == TOKYO_LINES
    assert lines(b)[-1] == "出典:東京都中央卸売市場 市場統計情報"
    assert rep.osaka.state == FAILED and rep.tokyo.state == NOT_YET
    assert_no_today_price(b)
    assert message.check_format(b.text) == []


def test_open_day_with_osaka_stopped(config, full):
    _, b = render(config, full, world_2026_10_25(), at(2026, 10, 23))
    assert lines(b)[0] == "Nothing needed - エリンギ 10/23(金)。大阪は停止中(503)。最新は東京9市場 9月 平均613円/kg"
    assert_no_today_price(b)


def test_not_yet_published(config, full):
    rep, b = render(config, full, world_2026_10_25(), at(2026, 10, 26))
    assert rep.day_status == cal.NOT_YET
    assert lines(b)[0].startswith("Nothing needed - エリンギ 10/26(月)。本日分は未公表。大阪は停止中(503)")
    assert_no_today_price(b)


def test_new_tokyo_month(config, store):
    seed_months(store, ["202509", "202608"])
    rep, b = render(config, store, world_2026_10_25(), at(2026, 10, 23))
    assert rep.tokyo.state == NEW
    assert "東京9月分が公表: 平均613円/kg 前月+12%" in lines(b)[0]
    assert lines(b)[1:3] == TOKYO_LINES
    assert store.has_tokyo("202609")


def test_first_run_fetches_the_months_it_needs(config, store):
    rep, b = render(config, store, world_2026_10_25(), at(2026, 10, 25))
    assert sorted(store.tokyo_index()) == ["202509", "202608", "202609"]
    assert lines(b)[1:3] == TOKYO_LINES


def test_new_tokyo_daily_row_is_today_only_on_its_own_date(config, full):
    text = real("tokyo_daily/sei_k2-2026-10-23.csv").decode("cp932")
    planted = text.replace("その他野菜,316970,", "エリンギ,1200,相対,1200,－,長　野,3,2160,1728,1296\r\nその他野菜,316970,", 1)
    w = world_2026_10_25(**{tokyo_daily.URL: (200, planted.encode("cp932"))})
    rep, b = render(config, full, w, at(2026, 10, 23))
    assert rep.tokyo_daily.state == NEW
    assert "東京大田(中値) 長野576" in b.text
    _, later = render(config, full, w, at(2026, 10, 26))
    assert "東京大田 前回10/23 長野 576" in later.text
    assert_no_today_price(later)


@pytest.mark.parametrize("over,label", [
    ((503, b"err"), "東京月次は取得失敗(503)"),
    ((200, b""), "東京月次は取得失敗(空ファイル)"),
    ((200, "<html>移転しました</html>".encode()), "東京月次は形式変更"),
    (OSError("timed out"), "東京月次は取得失敗(接続)"),
])
def test_tokyo_page_problems(config, full, over, label):
    _, b = render(config, full, world_2026_10_25(**{tokyo_monthly.PAGE_URL: over}), at(2026, 10, 23))
    assert label in lines(b)[0]
    assert lines(b)[1:3] == TOKYO_LINES  # the stored month, labelled with its month
    assert_no_today_price(b)
    assert message.check_format(b.text) == []


def planted_month(kind):
    rows = X.read_rows(xlsx("202609"))
    if kind == "header":
        rows[0] = [c.replace("産地", "出荷地") for c in rows[0]]
    if kind == "norow":
        rows = [r for r in rows if "エリンギ" not in "".join(r)]
    return X.make_xlsx(rows[:2000] if kind == "norow" else rows)


@pytest.mark.parametrize("body,label", [
    ("header", "東京月次は形式変更"),
    ("norow", "東京月次はエリンギ行なし"),
    ("empty", "東京月次は取得失敗(空ファイル)"),
    ("503", "東京月次は取得失敗(503)"),
])
def test_new_month_file_problems(config, store, body, label):
    seed_months(store, ["202509", "202608"])
    over = {"empty": (200, b""), "503": (503, b"")}.get(body) or (200, planted_month(body))
    rep, b = render(config, store, world_2026_10_25(**{BASE + "y202609meisai_20261015": over}), at(2026, 10, 23))
    assert rep.tokyo.state == FAILED
    assert label in lines(b)[0]
    assert not store.has_tokyo("202609")
    assert "9月" not in b.text  # nothing from the broken month is shown
    assert message.check_format(b.text) == []


@pytest.mark.parametrize("over,label", [
    ((503, b""), "市場カレンダーは取得失敗(503)"),
    ((200, b""), "市場カレンダーは取得失敗(空ファイル)"),
    ((200, b"<html>maintenance</html>"), "市場カレンダーは形式変更"),
])
def test_calendar_problems(config, full, over, label):
    rep, b = render(config, full, world_2026_10_25(**{cal.DATES_URL: over}), at(2026, 10, 23))
    assert label in lines(b)[0]
    assert rep.day_status == cal.UNKNOWN
    assert_no_today_price(b)


def test_osaka_back_is_awaiting_format(config, full):
    w = world_2026_10_25(**{osaka.URL: (200, "<html>市況</html>".encode())})
    rep, b = render(config, full, w, at(2026, 10, 23))
    assert rep.osaka.state == AWAITING_FORMAT
    assert "大阪は再開、形式確認待ち" in lines(b)[0]
    assert_no_today_price(b)


def test_osaka_404_on_closed_day_is_closed(config, full):
    rep, b = render(config, full, world_2026_10_25(**{osaka.URL: (404, b"")}), at(2026, 10, 25))
    assert rep.osaka.state == CLOSED
    assert lines(b)[0] == "Nothing needed - 10/25(日)は休市。最新は東京9市場 9月 平均613円/kg"


@pytest.mark.parametrize("over,label", [
    ((503, b""), "東京日報は取得失敗(503)"),
    ((200, b""), "東京日報は取得失敗(空ファイル)"),
    ((200, "販売結果（水産・大田）\r\n".encode("cp932")), "東京日報は形式変更"),
])
def test_tokyo_daily_problems(config, full, over, label):
    _, b = render(config, full, world_2026_10_25(**{tokyo_daily.URL: over}), at(2026, 10, 23))
    assert label in lines(b)[0]


def test_empty_store_and_tokyo_down(config, store):
    _, b = render(config, store, world_2026_10_25(**{tokyo_monthly.PAGE_URL: (503, b"")}), at(2026, 10, 23))
    assert lines(b)[0] == "Nothing needed - エリンギ 10/23(金)。大阪は停止中(503)。東京月次は取得失敗(503)"
    assert message.check_format(b.text) == []
