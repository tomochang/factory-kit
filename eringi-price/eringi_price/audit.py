"""`eringi-price audit` (R8): the KGI and K1-K5 from the run log and the stored raw files.

K1 every figure in a sent message equals the value recomputed from the stored raw file (sha256 checked), and its
   text is on the line it was recorded for.
K2 no price for an older date shown as today's; Tokyo months are the newest stored at run time; every 休市 day
   (recomputed from the stored MAFF pages) and every source problem is said on line 1.
K3 shown origins cover at least 95% of the volume.
K4 at most 700 characters and 10 lines, line 1 "Nothing needed - ".
K5 the canary log: every planted case caught.
KGI days of the trial window (28) with exactly one sent message by the deadline (17:30) that passes K1 and K2;
    target 27 of 28 and 0 stale-as-new days."""
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

from . import calendar as cal
from . import message, tokyo_daily, tokyo_monthly
from .report import LABELS
from .sources import AWAITING_FORMAT, FAILED

TRIAL_DAYS = 28
TARGET_DAYS = 27


def _sha_ok(raw):
    p = raw.get("path")
    return bool(p) and Path(p).exists() and hashlib.sha256(Path(p).read_bytes()).hexdigest() == raw.get("sha256")


def _tokyo_value(store, months, key):
    _, ym, kind = key.split(":", 2)
    if not store.verify_tokyo(ym):
        raise LookupError("sha256 of stored Tokyo %s does not match" % ym)
    s = tokyo_monthly.summary(months, ym)
    if kind == "avg":
        return s.avg
    if kind == "mom":
        return s.mom
    if kind == "yoy":
        return s.yoy
    if kind == "coverage":
        shown, _ = tokyo_monthly.pick_origins(s.month)
        return round(s.month.coverage([o.name for o in shown]) * 100, 1)
    if kind.startswith("origin:"):
        o = s.month.origin(kind.split(":", 1)[1])
        return o.price if o else None
    raise LookupError("unknown key " + key)


def _months_verified(store):
    """Parse only the stored months whose sha256 still matches (a changed file never feeds a figure)."""
    out = {}
    for ym in store.tokyo_index():
        if store.verify_tokyo(ym):
            out[ym] = tokyo_monthly.parse_meisai(store.tokyo_path(ym).read_bytes())
    return out


def _value(store, months, run, key):
    if key.startswith("tokyo:"):
        return _tokyo_value(store, months, key)
    src, market, origin, day, _ = key.split(":")
    if src == "tokyo_daily":
        raw = (run.get("sources", {}).get("tokyo_daily", {}).get("raw") or [{}])[0]
        if not _sha_ok(raw):
            raise LookupError("sha256 of stored Tokyo daily file does not match")
        n = tokyo_daily.parse_nippo(Path(raw["path"]).read_bytes())
        r = next((x for x in n.rows if x.origin == origin and n.date.isoformat() == day), None)
        return r.mid if r else None
    if src == "osaka":
        r = next((x for x in store.osaka_rows() if x.market == market and x.origin == origin
                  and x.date.isoformat() == day), None)
        return r.mid if r else None
    raise LookupError("unknown key " + key)


def _day_status_from_raw(run):
    raw = run.get("sources", {}).get("calendar", {}).get("raw") or []
    if len(raw) < 2 or not all(_sha_ok(r) for r in raw[:2]):
        return None
    try:
        c = cal.Calendar(cal.parse_open_dates(Path(raw[0]["path"]).read_text(encoding="utf-8", errors="replace")),
                         cal.parse_closed_dates(Path(raw[1]["path"]).read_text(encoding="utf-8", errors="replace")))
    except Exception:
        return None
    return c.status(date.fromisoformat(run["day"]))


def check_run(store, months, run):
    """(K1 mismatches, K2 stale-as-new count, K2 unreported list, coverages) for one run."""
    lines = run.get("message", "").split("\n")
    mism, stale, unrep, cover = [], 0, [], []
    for f in run.get("figures", []):
        try:
            v = _value(store, months, run, f["key"])
        except LookupError as e:
            mism.append("%s %s: %s" % (run["day"], f["key"], e))
            continue
        except Exception as e:  # unreadable raw file
            mism.append("%s %s: %s" % (run["day"], f["key"], type(e).__name__))
            continue
        if v != f["value"]:
            mism.append("%s %s: sent %s, source %s" % (run["day"], f["key"], f["value"], v))
        line = f.get("line")
        if f.get("text") and line is not None and (line >= len(lines) or f["text"] not in lines[line]):
            mism.append("%s %s: %r not on line %s" % (run["day"], f["key"], f["text"], line))
        if f["key"].endswith(":coverage"):
            cover.append(f["value"])
        if f.get("today") and f.get("as_of") != run["day"]:
            stale += 1
        if f["key"].startswith("tokyo:") and run.get("tokyo_newest") and f["as_of"] != run["tokyo_newest"]:
            stale += 1
    first = lines[0] if lines else ""
    status = _day_status_from_raw(run) or run.get("day_status")
    if status == cal.CLOSED and "休市" not in first:
        unrep.append("%s: 休市 not on line 1" % run["day"])
    for p in run.get("problems", []):
        if p not in first:
            unrep.append("%s: %s not on line 1" % (run["day"], p))
    for name, s in run.get("sources", {}).items():
        if s.get("state") in (FAILED, AWAITING_FORMAT) and ("%sは%s" % (LABELS.get(name, name), s.get("reason"))) \
                not in first:
            unrep.append("%s: %s %s not on line 1" % (run["day"], name, s.get("reason")))
    return mism, stale, unrep, cover


def _local(ts):
    return datetime.fromisoformat(ts)


def audit(store, include_dry_run=False, since=None, days=TRIAL_DAYS, deadline="17:30"):
    runs = [r for r in store.runs() if include_dry_run or (r.get("sent_at") and not r.get("dry_run"))]
    months = _months_verified(store)
    k1_checked, k1_mism, stale_total, unreported, covers, bad_fmt = 0, [], 0, [], [], []
    run_ok = {}
    for i, r in enumerate(runs):
        mism, stale, unrep, cover = check_run(store, months, r)
        k1_checked += len(r.get("figures", []))
        k1_mism += mism
        stale_total += stale
        unreported += unrep
        covers += cover
        fmt = message.check_format(r.get("message", ""))
        if fmt:
            bad_fmt.append("%s: %s" % (r["day"], "; ".join(fmt)))
        run_ok[i] = not mism and not stale and not unrep
    canary = store.canary_runs()
    k5_total = sum(c["total"] for c in canary)
    k5_caught = sum(c["caught"] for c in canary)

    sent = [(i, r) for i, r in enumerate(runs) if r.get("sent_at") and not r.get("dry_run")]
    start = date.fromisoformat(since) if since else (min(date.fromisoformat(r["day"]) for _, r in sent)
                                                    if sent else None)
    hh, mm = (int(x) for x in deadline.split(":"))
    good, bad_days, stale_days = 0, [], 0
    if start:
        for n in range(days):
            d = (start + timedelta(days=n)).isoformat()
            todays = [(i, r) for i, r in sent if r["day"] == d]
            if any(check_run(store, months, r)[1] for _, r in todays):
                stale_days += 1
            if len(todays) != 1:
                bad_days.append("%s: %d messages" % (d, len(todays)))
                continue
            i, r = todays[0]
            t = _local(r["sent_at"])
            if (t.hour, t.minute) > (hh, mm):
                bad_days.append("%s: sent %02d:%02d" % (d, t.hour, t.minute))
            elif not run_ok[i]:
                bad_days.append("%s: K1/K2 failed" % d)
            else:
                good += 1
    return {
        "KGI": {"days": days if start else 0, "good_days": good, "target": TARGET_DAYS, "start": start and
                start.isoformat(), "bad_days": bad_days, "stale_days": stale_days,
                "target_met": bool(start) and good >= TARGET_DAYS and stale_days == 0},
        "K1": {"checked": k1_checked, "mismatch": k1_mism, "ok": not k1_mism},
        "K2": {"stale_as_new": stale_total, "unreported": unreported, "ok": stale_total == 0 and not unreported},
        "K3": {"min_cover": min(covers) if covers else None, "ok": all(c >= 95.0 for c in covers)},
        "K4": {"messages": len(runs), "bad": bad_fmt, "ok": not bad_fmt},
        "K5": {"runs": len(canary), "caught": k5_caught, "total": k5_total,
               "ok": bool(canary) and k5_caught == k5_total},
    }


def format_audit(r):
    k = r["KGI"]
    ok = lambda b: "ok" if b else "NG"  # noqa: E731
    lines = [
        "Nothing needed - eringi-price audit: KGI %d/%d日 (目標%d, 古い値を新扱い%d日)%s" % (
            k["good_days"], k["days"], k["target"], k["stale_days"], " 達成" if k["target_met"] else ""),
        "K1 数値一致 %s (%d件, 不一致%d)" % (ok(r["K1"]["ok"]), r["K1"]["checked"], len(r["K1"]["mismatch"])),
        "K2 古い値なし %s (新扱い%d, 1行目漏れ%d)" % (ok(r["K2"]["ok"]), r["K2"]["stale_as_new"],
                                              len(r["K2"]["unreported"])),
        "K3 産地カバー %s (最小%s%%)" % (ok(r["K3"]["ok"]), r["K3"]["min_cover"]),
        "K4 形式 %s (%d通, NG%d)" % (ok(r["K4"]["ok"]), r["K4"]["messages"], len(r["K4"]["bad"])),
        "K5 canary %s (%d回, %d/%d検出)" % (ok(r["K5"]["ok"]), r["K5"]["runs"], r["K5"]["caught"], r["K5"]["total"]),
    ]
    probs = (r["K1"]["mismatch"] + r["K2"]["unreported"] + k["bad_days"])[:3]
    lines += [p[:80] for p in probs]
    return "\n".join(lines)
