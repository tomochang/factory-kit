"""K5 source change caught: a planted outage (503), empty file, changed header or missing エリンギ row is reported
on line 1 the same day, in 100% of fixture runs. `eringi-price canary` runs the same planted cases from its
own built-in fixtures (no network)."""
import io

from eringi_price import canary, cli, message


def test_every_planted_case_is_caught():
    results = canary.run_canary()
    names = {r.case for r in results}
    for src in ("tokyo_page", "tokyo_month", "calendar", "osaka", "tokyo_daily"):
        assert "%s:503" % src in names and "%s:empty" % src in names
    for case in ("tokyo_page:changed", "tokyo_month:header", "tokyo_month:norow", "calendar:changed",
                 "tokyo_daily:header", "osaka:back"):
        assert case in names
    missed = [(r.case, r.line1) for r in results if not r.caught]
    assert missed == []
    assert len(results) >= 16


def test_canary_cli_prints_the_share_and_logs_it(config):
    out = io.StringIO()
    rc = cli.main(["canary"], config=config, out=out)
    assert rc == 0
    assert "canary: " in out.getvalue() and "caught" in out.getvalue()
    from eringi_price.store import Store
    log = Store(config.data_dir).canary_runs()
    assert log and log[-1]["caught"] == log[-1]["total"]


def test_canary_fails_when_line1_stops_reporting(monkeypatch, config):
    real_build = message.build

    def silent(rep):
        rep.problems = []
        return real_build(rep)
    monkeypatch.setattr(message, "build", silent)
    out = io.StringIO()
    assert cli.main(["canary"], config=config, out=out) == 1
    assert "missed" in out.getvalue()
