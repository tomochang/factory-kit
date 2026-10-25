"""M1 Tokyo monthly (R1; KGI, K1, K3). Golden figures from the real 明細 files saved 2026-10-25; the hand check
is in ~/research/eringi-price/log.md and SPEC "Done when"."""
import pytest
from conftest import BASE, real, xlsx

from eringi_price import tokyo_monthly as tm
from eringi_price import xlsx as X
from eringi_price.sources import Empty, FormatChanged, NoItemRows


@pytest.fixture(scope="module")
def months():
    return {ym: tm.parse_meisai(xlsx(ym)) for ym in ("202509", "202608", "202609")}


def test_real_2026_09_golden(months):
    m = months["202609"]
    assert m.ym == "202609"
    assert m.kg == 167295
    assert m.price == 613
    by = {o.name: o for o in m.origins()}
    assert by["長野"].price == 558 and by["長野"].kg == 143406
    assert by["新潟"].price == 949
    assert by["香川"].price == 809 and by["広島"].price == 1171 and by["鳥取"].price == 748
    assert [o.name for o in m.origins()][:2] == ["長野", "新潟"]
    assert len(m.origins()) == 10
    assert m.n_markets == 9


def test_real_2026_08_and_2025_09(months):
    assert months["202608"].price == 547 and months["202608"].kg == 143739
    assert months["202509"].price == 561 and months["202509"].kg == 181137
    assert {o.name: o.price for o in months["202509"].origins()}["長野"] == 509


def test_month_on_month_and_year_on_year(months):
    s = tm.summary(months, "202609")
    assert (s.avg, s.mom, s.yoy) == (613, 12, 9)
    assert s.n_markets == 9


def test_missing_history_gives_none_not_zero(months):
    s = tm.summary({"202609": months["202609"]}, "202609")
    assert s.mom is None and s.yoy is None


def test_coverage_of_shown_origins_k3(months):
    m = months["202609"]
    assert round(m.coverage(["長野", "新潟"]) * 100, 1) == 97.8
    shown, rest = tm.pick_origins(m, max_shown=5, min_cover=0.95)
    assert [o.name for o in shown] == ["長野", "新潟", "香川", "広島", "鳥取"]
    assert rest == 5
    assert m.coverage([o.name for o in shown]) >= 0.95


def test_pick_origins_shows_more_than_max_when_needed_for_95_percent():
    rows = [tm.Row("大田市場", o, 100, 50000) for o in "ABCDEFGHIJ"]
    m = tm.Month("202601", rows)
    shown, rest = tm.pick_origins(m, max_shown=5, min_cover=0.95)
    assert m.coverage([o.name for o in shown]) >= 0.95
    assert rest == len(rows) - len(shown)


def test_price_is_rounded_half_up_whole_yen():
    assert tm.yen_per_kg(1005, 2) == 503  # 502.5 -> 503, not banker's 502
    assert tm.yen_per_kg(1004, 2) == 502
    assert tm.pct(613, 547) == 12 and tm.pct(613, 561) == 9 and tm.pct(95, 100) == -5


def test_links_found_by_label_and_y_prefix_on_the_real_page():
    links = tm.find_links(real("tokyo/geppo-2026-10-25.html.gz").decode("utf-8"))
    assert links["202609"] == BASE + "y202609meisai_20261015"
    assert links["202401"] == BASE + "y202401meisai"
    assert links["202607"] == BASE + "y202607meisai_2026081702"
    assert all(len(k) == 6 for k in links)  # yearly files (y2024meisai_...) are not months
    assert all("/s20" not in u for u in links.values())  # 水産 (s...) is never taken
    assert len([k for k in links if k >= "202401"]) == 33
    assert max(links) == "202609"


def test_links_on_a_changed_page_raise():
    with pytest.raises(FormatChanged):
        tm.find_links("<html><body>メンテナンス中</body></html>")
    with pytest.raises(Empty):
        tm.find_links("")


def test_changed_header_raises():
    rows = X.read_rows(xlsx("202609"))[:5]
    rows[0] = [c.replace("数量(kg)", "数量(t)") for c in rows[0]]
    with pytest.raises(FormatChanged):
        tm.parse_meisai(X.make_xlsx(rows))


def test_missing_item_rows_raise():
    rows = [r for r in X.read_rows(xlsx("202609")) if "エリンギ" not in "".join(r)][:50]
    with pytest.raises(NoItemRows):
        tm.parse_meisai(X.make_xlsx(rows))


def test_not_an_xlsx_and_empty():
    with pytest.raises(FormatChanged):
        tm.parse_meisai(b"<html>503</html>")
    with pytest.raises(Empty):
        tm.parse_meisai(b"")


def test_xlsx_reader_reads_the_real_header():
    head = X.read_rows(xlsx("202609"))[0]
    assert head[11] == "品目名称" and head[13] == "産地" and head[15] == "数量(kg)"
    assert X.read_rows(X.make_xlsx([["a", "b"], ["1", "エリンギだけ"]])) == [["a", "b"], ["1", "エリンギだけ"]]
