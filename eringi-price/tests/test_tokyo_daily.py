"""R3 Tokyo daily watch on the real 大田 販売結果 CSV (CP932) of 2026-10-23."""
from datetime import date

import pytest
from conftest import real

from eringi_price import tokyo_daily as td
from eringi_price.sources import Empty, FormatChanged

CSV = real("tokyo_daily/sei_k2-2026-10-23.csv")


def test_real_csv_date_market_and_no_item_row():
    n = td.parse_nippo(CSV)
    assert n.date == date(2026, 10, 23)
    assert n.market == "大田"
    assert n.rows == []  # エリンギ is inside 「その他野菜」 with no origin or price


def test_an_item_row_with_origin_and_price_is_kept():
    text = CSV.decode("cp932")
    planted = text.replace("その他野菜,316970,", "エリンギ,1200,相対,1200,－,長　野,3,2160,1728,1296\r\nその他野菜,316970,", 1)
    n = td.parse_nippo(planted.encode("cp932"))
    assert len(n.rows) == 1
    r = n.rows[0]
    assert (r.origin, r.high, r.mid, r.low) == ("長野", 720, 576, 432)  # yen per 3 kg -> yen/kg
    assert r.market == "大田" and r.date == date(2026, 10, 23)


def test_dash_values_are_none():
    text = CSV.decode("cp932").replace("その他野菜,316970,", "エリンギ,10,相対,10,－,新　潟,2,－,1000,－\r\nその他野菜,316970,", 1)
    r = td.parse_nippo(text.encode("cp932")).rows[0]
    assert (r.high, r.mid, r.low) == (None, 500, None)


def test_changed_and_empty():
    with pytest.raises(FormatChanged):
        td.parse_nippo("販売結果（水産・大田）\r\n".encode("cp932"))
    with pytest.raises(FormatChanged):
        td.parse_nippo(b"<html>maintenance</html>")
    with pytest.raises(Empty):
        td.parse_nippo(b"")
