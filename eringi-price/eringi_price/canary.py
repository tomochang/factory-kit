"""`eringi-price canary` (K5): run the whole pipeline on planted bad sources and check that line 1 reports each
one. Built-in fixtures only (no network); the Tokyo rows are the real 2026-09 大田 長野/新潟 rows."""
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import calendar as cal
from . import message, osaka, report, tokyo_daily, tokyo_monthly, xlsx
from .config import Config
from .fetch import Fetcher
from .store import Store

JST = timezone(timedelta(hours=9))
DAY = datetime(2026, 10, 26, 17, 0, tzinfo=JST)  # a Monday, open day in the planted calendar
DOC = "https://www.shijou.metro.tokyo.lg.jp/documents/d/shijou/"
HEADER = ["年月(yyyy/m) ネンゲツ", "年月(yyyymm) ", "年", "月", "市場コード", "市場", "大分類コード", "大分類",
          "中分類コード", "中分類", "品目コード", "品目名称", "産地コード", "産地", "国内外区分", "数量(kg)", "金額(円)エン"]


def _month(ym, kind="ok"):
    head = list(HEADER)
    if kind == "header":
        head[13] = "出荷地"
    rows = [head]
    base = ["0", ym, ym[:4], str(int(ym[4:])), "2", "大田市場", "3", "野　菜", "80", "きのこ山菜類"]
    rows.append(base + ["301000", "だいこん", "1", "北海道", "国内", "2159140", "271350378"])
    if kind != "norow":
        rows.append(base + ["386300", "エリンギだけ", "20", "長野", "国内", "85960", "42343343"])
        rows.append(base + ["386300", "エリンギだけ", "15", "新潟", "国内", "11665", "10992991"])
    return xlsx.make_xlsx(rows)


def _page():
    links = "".join('<li><a href="/documents/d/shijou/y%smeisai_x">市場統計情報明細データ(xlsx)</a></li>' % ym
                    for ym in ("202509", "202608", "202609"))
    return ('<label class="tab_class tab02" for="tab2">青果</label><ul>%s</ul>'
            '<label class="tab_class tab03" for="tab3">食肉</label>' % links).encode()


def base_world():
    nippo = "販売結果（青果・大田）\r\n令和8年10月26日(月曜日)\r\n\r\nその他野菜,316970,せり・入札,1155,－,－,－,－,－,－\r\n"
    w = {
        tokyo_monthly.PAGE_URL: (200, _page()),
        cal.DATES_URL: (200, b'<input type="hidden" name="s006.dataDate" value="20261026">'),
        cal.CLOSED_URL: (200, b'<input type="hidden" name="kyuichibi" value="20261025,20261028">'),
        tokyo_daily.URL: (200, nippo.encode("cp932")),
        osaka.URL: (503, b"<html>Error</html>"),
    }
    for ym in ("202509", "202608", "202609"):
        w[DOC + "y%smeisai_x" % ym] = (200, _month(ym))
    return w


MONTH_URL = DOC + "y202609meisai_x"
CASES = [
    ("tokyo_page:503", tokyo_monthly.PAGE_URL, (503, b""), "東京月次は取得失敗(503)"),
    ("tokyo_page:empty", tokyo_monthly.PAGE_URL, (200, b""), "東京月次は取得失敗(空ファイル)"),
    ("tokyo_page:changed", tokyo_monthly.PAGE_URL, (200, "<html>移転しました</html>".encode()), "東京月次は形式変更"),
    ("tokyo_month:503", MONTH_URL, (503, b""), "東京月次は取得失敗(503)"),
    ("tokyo_month:empty", MONTH_URL, (200, b""), "東京月次は取得失敗(空ファイル)"),
    ("tokyo_month:header", MONTH_URL, (200, _month("202609", "header")), "東京月次は形式変更"),
    ("tokyo_month:norow", MONTH_URL, (200, _month("202609", "norow")), "東京月次はエリンギ行なし"),
    ("calendar:503", cal.DATES_URL, (503, b""), "市場カレンダーは取得失敗(503)"),
    ("calendar:empty", cal.DATES_URL, (200, b""), "市場カレンダーは取得失敗(空ファイル)"),
    ("calendar:changed", cal.DATES_URL, (200, b"<html>maintenance</html>"), "市場カレンダーは形式変更"),
    ("osaka:503", osaka.URL, (503, b""), "大阪は停止中(503)"),
    ("osaka:empty", osaka.URL, (200, b""), "大阪は取得失敗(空ファイル)"),
    ("osaka:back", osaka.URL, (200, "<html>市況情報</html>".encode()), "大阪は再開、形式確認待ち"),
    ("tokyo_daily:503", tokyo_daily.URL, (503, b""), "東京日報は取得失敗(503)"),
    ("tokyo_daily:empty", tokyo_daily.URL, (200, b""), "東京日報は取得失敗(空ファイル)"),
    ("tokyo_daily:header", tokyo_daily.URL, (200, "販売結果（水産・大田）\r\n".encode("cp932")), "東京日報は形式変更"),
]


@dataclass
class Result:
    case: str
    caught: bool
    line1: str
    want: str


def run_canary():
    out = []
    for case, url, planted, want in CASES:
        world = base_world()
        world[url] = planted
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(data_dir=tmp, pause=0)
            store = Store(cfg.data_dir)
            for ym in ("202509", "202608"):
                store.put_tokyo(ym, DOC + "y%smeisai_x" % ym, _month(ym), at=DAY)
            fetcher = Fetcher(cfg.data_dir / "raw", cfg.user_agent, pause=0,
                              transport=lambda u, h, w=world: w.get(u, (404, b"")), clock=lambda: DAY)
            rep = report.collect(fetcher, store, cfg, DAY)
            first = message.build(rep).text.split("\n")[0]
        caught = first.startswith(message.PREFIX) and want in first and "(中値)" not in first
        out.append(Result(case, caught, first, want))
    return out
