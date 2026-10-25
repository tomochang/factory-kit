"""The daily message (R6, K4) and its only way out: ask-me --tell to the owner's own chat.

At most 700 characters and 10 lines; line 1 starts "Nothing needed - " and says the day, 休市, every source
problem and the headline. A price is "today's" only when its source has new data for today; anything older
carries its month or 「前回 M/D」."""
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List

from . import calendar as cal
from . import tokyo_monthly
from .sources import NEW

PREFIX = "Nothing needed - "
MAX_CHARS = 700
MAX_LINES = 10
WD = "月火水木金土日"
CITE = {"osaka": "大阪市中央卸売市場 市況情報", "tokyo": "東京都中央卸売市場 市場統計情報",
        "tokyo_daily": "東京都中央卸売市場日報"}


@dataclass
class Built:
    text: str
    figures: List[dict] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


def md(d):
    return "%d/%d" % (d.month, d.day)


def yen(v):
    return "－" if v is None else "{:,}".format(v)


def signed(v):
    return "%+d%%" % v


def daily_label(line):
    return ("大阪" if line.source == "osaka" else "東京") + line.market


def osaka_line(l):
    """One daily line (Osaka or Tokyo daily). Today's: 「大阪本場(中値) 新潟1,080 先週+8% 前年(東京月次)+31% 上昇」;
    older: 「大阪東部 前回10/24 新潟 972」."""
    if not l.today:
        return "%s 前回%s %s %s" % (daily_label(l), md(l.as_of), l.origin, yen(l.price))
    parts = ["%s(中値) %s%s" % (daily_label(l), l.origin, yen(l.price))]
    if l.week is not None:
        parts.append("先週" + signed(l.week))
    if l.year is not None:
        parts.append("前年%s%s" % ("(%s)" % l.year_label if l.year_label else "", signed(l.year)))
    if l.trend:
        parts.append(l.trend)
    return " ".join(parts)


def _headline(rep):
    today = [l for l in rep.osaka_lines if l.today]
    if today:
        l = today[0]
        h = "%s %s%s円/kg" % (daily_label(l), l.origin, yen(l.price))
        return h + (" 先週比" + signed(l.week) if l.week is not None else "")
    if rep.tokyo.state == NEW and rep.tokyo_newest in rep.tokyo_months:
        s = tokyo_monthly.summary(rep.tokyo_months, rep.tokyo_newest)
        h = "東京%d月分が公表: 平均%s円/kg" % (int(s.ym[4:]), yen(s.avg))
        return h + (" 前月" + signed(s.mom) if s.mom is not None else "")
    if rep.osaka_lines:
        l = max(rep.osaka_lines, key=lambda x: (x.as_of, x.market == "本場"))
        return "最新は%s: %s %s%s円/kg" % (md(l.as_of), daily_label(l), l.origin, yen(l.price))
    if rep.tokyo_newest in rep.tokyo_months:
        s = tokyo_monthly.summary(rep.tokyo_months, rep.tokyo_newest)
        return "最新は東京%d市場 %d月 平均%s円/kg" % (s.n_markets, int(s.ym[4:]), yen(s.avg))
    return None


def line1(rep):
    d = "%s(%s)" % (md(rep.day), WD[rep.day.weekday()])
    closed = rep.day_status == cal.CLOSED
    head = "%sは休市" % d if closed else "エリンギ %s" % d
    headline = _headline(rep)
    if closed or rep.problems:
        text = "。".join([head] + list(rep.problems) + ([headline] if headline else []))
    else:
        text = head + (" " + headline if headline else "")
    return PREFIX + text


def build(rep):
    lines, figures, cites = [line1(rep)], [], []

    def fig(key, value, text, as_of, today=False, line=None):
        figures.append({"key": key, "value": value, "text": text, "as_of": as_of, "today": today,
                        "line": len(lines) if line is None else line})

    for l in rep.osaka_lines:
        fig("osaka:%s:%s:%s:mid" % (l.market, l.origin, l.as_of.isoformat()), l.price, l.origin, l.as_of.isoformat(),
            l.today)
        lines.append(osaka_line(l))
        if "osaka" not in cites:
            cites.append("osaka")
    if rep.tokyo_newest in rep.tokyo_months:
        s = tokyo_monthly.summary(rep.tokyo_months, rep.tokyo_newest)
        ym, mon = s.ym, int(s.ym[4:])
        parts = ["東京%d市場 %d月 平均%s" % (s.n_markets, mon, yen(s.avg))]
        fig("tokyo:%s:avg" % ym, s.avg, "平均" + yen(s.avg), ym)
        if s.mom is not None:
            parts.append("前月" + signed(s.mom))
            fig("tokyo:%s:mom" % ym, s.mom, "前月" + signed(s.mom), ym)
        if s.yoy is not None:
            parts.append("前年" + signed(s.yoy))
            fig("tokyo:%s:yoy" % ym, s.yoy, "前年" + signed(s.yoy), ym)
        lines.append(" ".join(parts))
        shown, rest = tokyo_monthly.pick_origins(s.month)
        for o in shown:
            fig("tokyo:%s:origin:%s" % (ym, o.name), o.price, o.name + yen(o.price), ym)
        cover = round(s.month.coverage([o.name for o in shown]) * 100, 1)
        fig("tokyo:%s:coverage" % ym, cover, "", ym)
        lines.append(" ".join(o.name + yen(o.price) for o in shown) + (" 他%d産地" % rest if rest else ""))
        cites.append("tokyo")
    for l in rep.tokyo_daily_lines:
        fig("tokyo_daily:%s:%s:%s:mid" % (l.market, l.origin, l.as_of.isoformat()), l.price, l.origin,
            l.as_of.isoformat(), l.today)
        lines.append(osaka_line(l))
        if "tokyo_daily" not in cites:
            cites.append("tokyo_daily")
    if cites:
        lines.append("出典:" + "/".join(CITE[c] for c in ("osaka", "tokyo", "tokyo_daily") if c in cites))
    return Built("\n".join(lines), figures, list(rep.problems))


def check_format(text):
    out = []
    lines = text.split("\n")
    if len(text) > MAX_CHARS:
        out.append("over %d chars (%d)" % (MAX_CHARS, len(text)))
    if len(lines) > MAX_LINES:
        out.append("over %d lines (%d)" % (MAX_LINES, len(lines)))
    if not lines[0].startswith(PREFIX):
        out.append('line 1 must start "%s"' % PREFIX)
    return out


def send(text, config, dry_run=False, runner=subprocess.run, out=None):
    """Send to his own chat with ask-me --tell (never anyone else). dry_run prints instead."""
    out = out or sys.stdout
    bad = check_format(text)
    if bad:
        raise ValueError("message refused: " + "; ".join(bad))
    if dry_run:
        out.write("[dry-run] not sent; would run ask-me --from %s --tell with:\n%s\n" % (config.sender, text))
        return True
    argv = [sys.executable, config.ask_me, "--from", config.sender, "--tell", text]
    res = runner(argv, timeout=120, capture_output=True, text=True)
    return getattr(res, "returncode", 1) == 0
