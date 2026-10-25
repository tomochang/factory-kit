"""Tokyo daily watch (R3): the 東京都中央卸売市場日報 青果 販売結果 CSV (CP932) for 大田, from the temporary page
used since 2026-10-23. Today エリンギ is only inside 「その他野菜」 (no origin, no price), so normally no row is
found and nothing is reported; if a row ever appears with an origin and a price it becomes a daily Tokyo line.

Columns (budou-shijou reads the same files, REFERENCE items 7-9): 品目, 品目計, 販売方法, 数量, 品種, 産地,
量目(kg), 高値, 中値, 安値; empty upper cells repeat the row above; "－" means no value."""
import csv
import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from .sources import Empty, FormatChanged

URL = "https://www.shijou.metro.tokyo.lg.jp/documents/d/shijou/sei_k2"
DASH = "－"


@dataclass
class DailyRow:
    date: date
    market: str
    origin: str
    high: Optional[int]
    mid: Optional[int]
    low: Optional[int]
    volume: Optional[float] = None


@dataclass
class Nippo:
    date: date
    market: str
    rows: List[DailyRow]


def _num(v):
    v = (v or "").replace(",", "").strip()
    if not v or v in (DASH, "-"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _reiwa(line):
    m = re.search(r"令和\s*(\d+|元)年\s*(\d+)月\s*(\d+)日", line)
    if not m:
        return None
    y = 1 if m.group(1) == "元" else int(m.group(1))
    return date(2018 + y, int(m.group(2)), int(m.group(3)))


def parse_nippo(data, item="エリンギ"):
    if not data:
        raise Empty()
    try:
        text = data.decode("cp932")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise Empty()
    m = re.match(r"\s*販売結果（青果・(.+?)）", lines[0])
    if not m:
        raise FormatChanged("line 1 is not 販売結果（青果・…）")
    day = _reiwa(lines[1]) if len(lines) > 1 else None
    if day is None:
        raise FormatChanged("no 令和 date on line 2")
    market, carry, rows = m.group(1), ["", "", "", ""], []
    for row in csv.reader(lines[2:]):
        if len(row) < 10:
            continue
        for i in range(4):
            if row[i].strip():
                carry[i] = row[i].strip()
        if item not in carry[0]:
            continue
        origin = re.sub(r"[\s　]", "", row[5])
        unit = _num(row[6])
        prices = [_num(c) for c in row[7:10]]
        if not origin or origin == DASH or not unit or all(p is None for p in prices):
            continue
        hi, mid, lo = [int(round(p / unit)) if p is not None else None for p in prices]
        rows.append(DailyRow(day, market, origin, hi, mid, lo, _num(carry[3])))
    return Nippo(day, market, rows)
