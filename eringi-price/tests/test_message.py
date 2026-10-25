"""M4 the 17:00 message (R6; KGI, K4): at most 700 characters and 10 lines, line 1 "Nothing needed - ", the
sample lines from the real 2026-09 Tokyo figures, the longest case, and sending only through ask-me --tell
(tests use --dry-run or a stub; nothing is ever sent)."""
import io
import sys
from datetime import date, timedelta

import pytest
from conftest import at, fetcher_for, seed_months, world_2026_10_25

from eringi_price import message, osaka, report


@pytest.fixture
def rep(config, store):
    seed_months(store, ["202509", "202608", "202609"])
    f, _ = fetcher_for(config, world_2026_10_25())
    return report.collect(f, store, config, at(2026, 10, 25))


def test_real_tokyo_sample_lines(rep):
    t = message.build(rep).text.split("\n")
    assert "東京9市場 9月 平均613 前月+12% 前年+9%" in t
    assert "長野558 新潟949 香川809 広島1,171 鳥取748 他5産地" in t


def test_figures_are_recorded_for_the_audit(rep):
    figs = {f["key"]: f for f in message.build(rep).figures}
    assert figs["tokyo:202609:avg"]["value"] == 613
    assert figs["tokyo:202609:mom"]["value"] == 12
    assert figs["tokyo:202609:yoy"]["value"] == 9
    assert figs["tokyo:202609:origin:広島"]["value"] == 1171
    assert figs["tokyo:202609:coverage"]["value"] >= 95.0


def osaka_history(day, origins, markets=osaka.MARKETS, weeks=60):
    rows, d, i = [], day - timedelta(weeks=weeks), 0
    while d <= day:
        if d.weekday() not in (2, 6):
            for m in markets:
                for k, o in enumerate(origins):
                    rows.append(osaka.Row(d, m, o, 1300 + k, 1000 + i % 7 + k * 10, 800, 500 - k))
            i += 1
        d += timedelta(days=1)
    return rows


def test_longest_case_fits(rep):
    """10 Tokyo origins plus 2 Osaka markets with their main origins, every part present."""
    day = date(2026, 10, 27)
    rep.day, rep.day_status = day, "open"
    rows = osaka_history(day, ["新潟", "長野", "香川"])
    rep.osaka_lines = report.osaka_lines(rows, day, rep.tokyo_months)
    rep.problems = ["東京日報は取得失敗(503)", "市場カレンダーは形式変更"]
    b = message.build(rep)
    assert len(rep.tokyo_months["202609"].origins()) == 10
    assert message.check_format(b.text) == [], b.text
    assert "大阪本場(中値)" in b.text and "大阪東部(中値)" in b.text
    assert b.text.split("\n")[-1] == "出典:大阪市中央卸売市場 市況情報/東京都中央卸売市場 市場統計情報"
    assert len(b.text) <= 700 and len(b.text.split("\n")) <= 10


def test_osaka_lines_shape_and_tokyo_fallback_for_last_year(rep):
    day = date(2026, 10, 27)
    rows = osaka_history(day, ["新潟", "長野"], weeks=8)  # less than a year: last year from Tokyo monthly
    lines = report.osaka_lines(rows, day, rep.tokyo_months)
    honjo = [l for l in lines if l.market == "本場"]
    assert [l.origin for l in honjo] == ["新潟", "長野"]
    assert honjo[0].year_label == "東京月次" and honjo[0].year == 31  # 新潟 949 vs 724 (2025-09)
    assert honjo[1].year == 10  # 長野 558 vs 509
    text = message.osaka_line(honjo[0])
    assert text.startswith("大阪本場(中値) 新潟1,0") and "前年(東京月次)+31%" in text


def test_stale_osaka_market_is_labelled_previous(rep):
    day = date(2026, 10, 27)
    rows = [r for r in osaka_history(day, ["新潟"]) if not (r.market == "東部" and r.date == day)]
    lines = report.osaka_lines(rows, day, rep.tokyo_months)
    tobu = [l for l in lines if l.market == "東部"][0]
    assert not tobu.today
    assert message.osaka_line(tobu).startswith("大阪東部 前回10/26 新潟 ")


def test_closed_day_with_osaka_history(rep):
    day = date(2026, 10, 28)
    rows = osaka_history(date(2026, 10, 27), ["新潟"])
    rep.day, rep.day_status, rep.problems = day, "closed", []
    rep.osaka_lines = report.osaka_lines(rows, day, rep.tokyo_months)
    b = message.build(rep)
    first = b.text.split("\n")[0]
    assert first.startswith("Nothing needed - 10/28(水)は休市。最新は10/27: 大阪本場 新潟1,")
    assert not any(f.get("today") for f in b.figures)


@pytest.mark.parametrize("text,problem", [
    ("Nothing needed - x\n" + "a" * 700, "chars"),
    ("Nothing needed - x" + "\nb" * 10, "lines"),
    ("You: merge\nb", "line 1"),
])
def test_check_format_catches_each_rule(text, problem):
    assert any(problem in p for p in message.check_format(text))


def test_dry_run_prints_and_never_calls_ask_me(config):
    out, calls = io.StringIO(), []
    ok = message.send("Nothing needed - test", config, dry_run=True, runner=lambda *a, **k: calls.append(a), out=out)
    assert ok and calls == []
    assert "Nothing needed - test" in out.getvalue() and "dry-run" in out.getvalue()


def test_send_uses_ask_me_tell_only(config):
    calls = []

    class R:
        returncode = 0
    ok = message.send("Nothing needed - test", config, dry_run=False,
                      runner=lambda argv, **k: calls.append(argv) or R())
    assert ok
    assert calls == [[sys.executable, config.ask_me, "--from", "eringi-price", "--tell", "Nothing needed - test"]]


def test_send_refuses_a_message_that_breaks_the_format(config):
    calls = []
    with pytest.raises(ValueError):
        message.send("You: hi", config, dry_run=False, runner=lambda *a, **k: calls.append(a))
    assert calls == []
