"""eringi-price: a daily エリンギ wholesale price report by origin prefecture, to his own chat only.

  eringi-price run [--dry-run] [--force]   read the sources, build the message, send it with ask-me --tell
                                           (at most one sent message per day; --dry-run prints only)
  eringi-price backfill [--from YYYY-MM]   store every Tokyo monthly 明細 file from 2024-01 (once)
  eringi-price audit [--include-dry-run] [--since YYYY-MM-DD] [--json]   KGI and K1-K5 from the run log
  eringi-price canary                      planted bad sources: is each one reported on line 1?
  eringi-price install [--print] | uninstall   the daily LaunchAgent (installing is the owner's step)
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import audit as audit_mod
from . import canary as canary_mod
from . import install as install_mod
from . import message, report
from .config import Config
from .fetch import Fetcher
from .sources import FAILED
from .store import Store

JST = timezone(timedelta(hours=9))
PROGRAM = Path(__file__).resolve().parents[1] / "eringi-price"


def _canary(store, out):
    results = canary_mod.run_canary()
    missed = [r for r in results if not r.caught]
    store.append_canary({"at": datetime.now(JST).isoformat(), "total": len(results),
                         "caught": len(results) - len(missed), "missed": [r.case for r in missed]})
    out.write("canary: %d/%d caught\n" % (len(results) - len(missed), len(results)))
    for r in missed:
        out.write("  missed %s: want %r, line 1 was %r\n" % (r.case, r.want, r.line1))
    return missed, len(results)


def cmd_run(args, config, store, fetcher, now, out):
    t0 = time.monotonic()
    day = now.date()
    if not args.dry_run and not args.force and store.sent_on(day):
        out.write("already sent for %s; nothing to do\n" % day.isoformat())
        return 0
    rep = report.collect(fetcher, store, config, now)
    if now.weekday() == config.canary_weekday:
        missed, total = _canary(store, out)
        if missed:
            rep.problems.append("canary NG(%d/%d)" % (len(missed), total))
    built = message.build(rep)
    ok = message.send(built.text, config, dry_run=args.dry_run, out=out)
    sent_at = now + timedelta(seconds=time.monotonic() - t0) if (ok and not args.dry_run) else None
    store.append_run({
        "day": day.isoformat(), "run_at": now.isoformat(), "sent_at": sent_at.isoformat() if sent_at else None,
        "dry_run": bool(args.dry_run), "message": built.text, "day_status": rep.day_status,
        "problems": built.problems, "tokyo_newest": rep.tokyo_newest,
        "sources": {k: v.to_json() for k, v in rep.sources().items()}, "figures": built.figures,
    })
    if not args.dry_run:
        out.write(("sent:\n" if ok else "SEND FAILED:\n") + built.text + "\n")
    return 0 if ok else 1


def cmd_backfill(args, config, store, fetcher, now, out):
    since = (args.since or config.backfill_from).replace("-", "")
    before = set(store.tokyo_index())
    page = fetcher.get(report.tokyo_monthly.PAGE_URL)
    try:
        links = report.tokyo_monthly.find_links(page.body.decode("utf-8", errors="replace")) if page.ok else {}
    except Exception:
        links = {}
    want = sorted(ym for ym in links if ym >= since)
    res, _ = report.update_tokyo(fetcher, store, config, now, want=want)
    idx = store.tokyo_index()
    have = sorted(ym for ym in idx if ym >= since)
    out.write("back-fill: %d months stored (%s-%s), %d new this run\n" % (
        len(have), have[0] if have else "-", have[-1] if have else "-", len(set(idx) - before)))
    if res.state == FAILED:
        out.write("problem: 東京月次は%s (%s)\n" % (res.reason, res.as_of or "page"))
        return 1
    return 0


def cmd_audit(args, config, store, fetcher, now, out):
    r = audit_mod.audit(store, include_dry_run=args.include_dry_run, since=args.since, deadline=config.deadline)
    out.write((json.dumps(r, ensure_ascii=False, indent=1) if args.json else audit_mod.format_audit(r)) + "\n")
    return 0


def cmd_canary(args, config, store, fetcher, now, out):
    missed, _ = _canary(store, out)
    return 1 if missed else 0


def cmd_install(args, config, store, fetcher, now, out):
    if args.print:
        out.write(install_mod.render_plist(config, PROGRAM))
        return 0
    p = install_mod.install(config, PROGRAM)
    out.write("installed %s (daily %02d:%02d)\n" % (p, config.send_hour, config.send_minute))
    return 0


def cmd_uninstall(args, config, store, fetcher, now, out):
    out.write("removed %s\n" % install_mod.uninstall(config))
    return 0


def main(argv=None, config=None, fetcher=None, now=None, out=None):
    ap = argparse.ArgumentParser(prog="eringi-price", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="send even if a message was already sent today")
    p = sub.add_parser("backfill")
    p.add_argument("--from", dest="since")
    p = sub.add_parser("audit")
    p.add_argument("--include-dry-run", action="store_true")
    p.add_argument("--since")
    p.add_argument("--json", action="store_true")
    sub.add_parser("canary")
    p = sub.add_parser("install")
    p.add_argument("--print", action="store_true", help="print the plist, do not install")
    sub.add_parser("uninstall")
    args = ap.parse_args(argv)
    config = config or Config.from_env()
    out = out or sys.stdout
    now = now or datetime.now(JST)
    store = Store(config.data_dir)
    if fetcher is None and args.cmd in ("run", "backfill"):
        fetcher = Fetcher(config.data_dir / "raw", config.user_agent, pause=config.pause)
    cmd = {"run": cmd_run, "backfill": cmd_backfill, "audit": cmd_audit, "canary": cmd_canary,
           "install": cmd_install, "uninstall": cmd_uninstall}[args.cmd]
    return cmd(args, config, store, fetcher, now, out)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
