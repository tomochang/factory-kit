"""東京都中央卸売市場 市場統計情報明細データ (R1): monthly kg and yen per market x origin.

Links are found on the 月報・年報 page by their label and the 青果 `y` prefix, never by a fixed file name,
because file names carry the publish date (tokyo-shijou-geppo-page.md)."""
import hashlib
import html as htmllib
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional

from . import xlsx
from .sources import Empty, FormatChanged, NoItemRows

PAGE_URL = "https://www.shijou.metro.tokyo.lg.jp/torihiki/geppo"
SITE = "https://www.shijou.metro.tokyo.lg.jp"
LABEL = "市場統計情報明細データ"
# header columns we read; matched by prefix because the real header has readings appended ("金額(円)エン")
NEED = {"ym": "年月(yyyymm)", "market": "市場", "code": "品目コード", "item": "品目名称", "origin": "産地",
        "kg": "数量(kg)", "yen": "金額(円)"}


def yen_per_kg(yen, kg):
    return int((Decimal(yen) / Decimal(kg)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def pct(new, old):
    """Whole-percent change, rounded half away from zero."""
    v = (Decimal(new) / Decimal(old) - 1) * 100
    return int(v.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass
class Row:
    market: str
    origin: str
    kg: int
    yen: int


@dataclass
class Origin:
    name: str
    kg: int
    yen: int

    @property
    def price(self):
        return yen_per_kg(self.yen, self.kg)


class Month:
    def __init__(self, ym, rows):
        self.ym = ym
        self.rows = list(rows)

    @property
    def kg(self):
        return sum(r.kg for r in self.rows)

    @property
    def yen(self):
        return sum(r.yen for r in self.rows)

    @property
    def price(self):
        return yen_per_kg(self.yen, self.kg)

    @property
    def n_markets(self):
        return len({r.market for r in self.rows})

    def origins(self) -> List[Origin]:
        acc = {}
        for r in self.rows:
            o = acc.setdefault(r.origin, Origin(r.origin, 0, 0))
            o.kg += r.kg
            o.yen += r.yen
        return sorted((o for o in acc.values() if o.kg > 0), key=lambda o: (-o.kg, o.name))

    def coverage(self, names):
        names = set(names)
        return sum(o.kg for o in self.origins() if o.name in names) / self.kg

    def origin(self, name) -> Optional[Origin]:
        return next((o for o in self.origins() if o.name == name), None)


def _cols(header):
    cols = {}
    for key, want in NEED.items():
        for i, h in enumerate(header):
            h = (h or "").strip()
            if key == "market" and h != "市場":
                continue
            if key == "origin" and h != "産地":
                continue
            if h.startswith(want):
                cols[key] = i
                break
    missing = [NEED[k] for k in NEED if k not in cols]
    if missing:
        raise FormatChanged("header changed, missing " + ", ".join(missing))
    return cols


_ROWS = {}  # sha256 -> rows: the real sheets are 3-4 MB of XML, parse each file once per process


def parse_meisai(data, item="エリンギ", code="386300"):
    if not data:
        raise Empty()
    key = hashlib.sha256(data).hexdigest()
    try:
        rows = _ROWS[key] if key in _ROWS else _ROWS.setdefault(key, xlsx.read_rows(data))
    except ValueError as e:
        raise FormatChanged(str(e))
    if not rows:
        raise Empty()
    cols = _cols(rows[0])
    out, ym = [], None
    for r in rows[1:]:
        get = lambda k: (r[cols[k]] if cols[k] < len(r) else "").strip()  # noqa: E731
        if get("code") != code and item not in get("item"):
            continue
        try:
            kg, yen = int(float(get("kg") or 0)), int(float(get("yen") or 0))
        except ValueError:
            raise FormatChanged("number expected in 数量/金額")
        ym = ym or get("ym")
        out.append(Row(get("market"), get("origin"), kg, yen))
    if not out or not sum(r.kg for r in out):
        raise NoItemRows()
    return Month(ym, out)


def find_links(page):
    """{"YYYYMM": absolute URL} of the monthly 青果 明細 files."""
    if not page or not page.strip():
        raise Empty()
    start = page.find(">青果</label")
    if start < 0:
        raise FormatChanged("no 青果 tab")
    nxt = page.find('class="tab_class', start)  # the next tab (食肉) ends the 青果 section
    section = page[start: nxt if nxt > 0 else len(page)]
    links = {}
    for href, label in re.findall(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', section, re.S):
        label = htmllib.unescape(re.sub(r"<[^>]+>", "", label)).strip()
        m = re.search(r"/y(\d{6})meisai[^/]*$", href)
        if LABEL in label and m:
            links[m.group(1)] = href if href.startswith("http") else SITE + href
    if not links:
        raise FormatChanged("no 明細 links")
    return links


@dataclass
class Summary:
    ym: str
    avg: int
    mom: Optional[int]
    yoy: Optional[int]
    n_markets: int
    month: Month
    prev: Optional[Month]
    year_ago: Optional[Month]


def prev_ym(ym, months=1):
    y, m = int(ym[:4]), int(ym[4:])
    m -= months
    while m <= 0:
        y, m = y - 1, m + 12
    return "%04d%02d" % (y, m)


def summary(months, ym):
    cur = months[ym]
    prev, ago = months.get(prev_ym(ym)), months.get(prev_ym(ym, 12))
    return Summary(ym, cur.price, pct(cur.price, prev.price) if prev else None,
                   pct(cur.price, ago.price) if ago else None, cur.n_markets, cur, prev, ago)


def pick_origins(month, max_shown=5, min_cover=0.95):
    """Origins to show: the top max_shown by volume, more if needed to cover min_cover of the volume."""
    origins = month.origins()
    shown, kg = [], 0
    for o in origins:
        if len(shown) >= max_shown and kg / month.kg >= min_cover:
            break
        shown.append(o)
        kg += o.kg
    return shown, len(origins) - len(shown)
