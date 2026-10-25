"""CLI: run (dry-run and a stub ask-me), one message per day, back-fill from 2024-01 (R1), and the LaunchAgent
(R9) rendered and installed with a fake launchctl only. Nothing here sends or loads anything for real."""
import io
import json
import sys

from conftest import BASE, at, fetcher_for, seed_months, world_2026_10_25, xlsx

from eringi_price import cli, install, tokyo_monthly
from eringi_price import xlsx as X
from eringi_price.store import Store


def stub_ask_me(tmp_path):
    log = tmp_path / "ask-me-calls.jsonl"
    stub = tmp_path / "ask-me-stub.py"
    stub.write_text("import json, sys\nopen(%r, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n" % str(log))
    return stub, log


def test_run_dry_run_prints_and_logs_without_sending(config, store):
    seed_months(store, ["202509", "202608", "202609"])
    f, _ = fetcher_for(config, world_2026_10_25())
    out = io.StringIO()
    assert cli.main(["run", "--dry-run"], config=config, fetcher=f, now=at(2026, 10, 25), out=out) == 0
    assert "Nothing needed - 10/25(日)は休市" in out.getvalue()
    runs = store.runs()
    assert len(runs) == 1 and runs[0]["dry_run"] and runs[0]["sent_at"] is None
    assert runs[0]["sources"]["tokyo"]["raw"][0]["sha256"]


def test_run_sends_once_per_day_through_ask_me(config, store, tmp_path):
    stub, log = stub_ask_me(tmp_path)
    config.ask_me = str(stub)
    seed_months(store, ["202509", "202608", "202609"])
    for hour in (17, 18):
        f, _ = fetcher_for(config, world_2026_10_25())
        assert cli.main(["run"], config=config, fetcher=f, now=at(2026, 10, 25, hour), out=io.StringIO()) == 0
    calls = [json.loads(l) for l in log.read_text().splitlines()]
    assert len(calls) == 1
    assert calls[0][:4] == ["--from", "eringi-price", "--tell", calls[0][3]]
    assert calls[0][3].startswith("Nothing needed - 10/25(日)は休市")
    sent = [r for r in store.runs() if r["sent_at"]]
    assert len(sent) == 1


def small_month(ym):
    head = X.read_rows(xlsx("202609"))[0]
    row = ["0", ym, ym[:4], str(int(ym[4:])), "2", "大田市場", "3", "野\u3000菜", "80", "きのこ山菜類",
           "386300", "エリンギだけ", "20", "長野", "国内", "1000", "550000"]
    return X.make_xlsx([head, row])


def test_backfill_from_2024_01_politely(config, store):
    page = world_2026_10_25()[tokyo_monthly.PAGE_URL][1]
    links = tokyo_monthly.find_links(page.decode())
    world = {tokyo_monthly.PAGE_URL: (200, page)}
    for ym, url in links.items():
        world[url] = (200, xlsx(ym) if url.endswith(("20251017", "20260914", "20261015")) else small_month(ym))
    config.pause = 2.0
    sleeps = []
    f, t = fetcher_for(config, world, sleeps)
    out = io.StringIO()
    assert cli.main(["backfill"], config=config, fetcher=f, out=out) == 0
    idx = store.tokyo_index()
    assert sorted(idx)[0] == "202401" and sorted(idx)[-1] == "202609" and len(idx) == 33
    assert all(v["sha256"] for v in idx.values())
    assert len(t.calls) == 34 and sleeps == [2.0] * 33
    f2, t2 = fetcher_for(config, world)
    assert cli.main(["backfill"], config=config, fetcher=f2, out=io.StringIO()) == 0
    assert [u for u, _ in t2.calls] == [tokyo_monthly.PAGE_URL]  # nothing fetched twice
    assert "33" in out.getvalue()


def test_plist_runs_daily_at_send_time_through_this_cli(config):
    config.send_hour, config.send_minute = 17, 0
    text = install.render_plist(config, program="/x/eringi-price")
    assert "<key>Hour</key><integer>17</integer>" in text and "<key>Minute</key><integer>0</integer>" in text
    assert "<string>/x/eringi-price</string>" in text and "<string>run</string>" in text
    assert "--dry-run" not in text
    assert config.label in text and str(config.data_dir) in text and config.ask_me in text


def test_install_uses_launchctl_only_when_asked(config, tmp_path):
    calls = []
    agents = tmp_path / "LaunchAgents"
    plist = install.install(config, program="/x/eringi-price", agents=agents,
                            launchctl=lambda *a: calls.append(a) or (0, "", ""))
    assert plist.exists() and plist.name == config.label + ".plist"
    assert calls and calls[0][0] == "bootstrap"
    out = io.StringIO()
    assert cli.main(["install", "--print"], config=config, out=out) == 0
    assert "<plist" in out.getvalue()


def test_audit_command_runs(config, store):
    seed_months(store, ["202509", "202608", "202609"])
    f, _ = fetcher_for(config, world_2026_10_25())
    cli.main(["run", "--dry-run"], config=config, fetcher=f, now=at(2026, 10, 25), out=io.StringIO())
    out = io.StringIO()
    assert cli.main(["audit", "--include-dry-run"], config=config, out=out) == 0
    for k in ("KGI", "K1", "K2", "K3", "K4", "K5"):
        assert k in out.getvalue()


def test_entry_script_exists_and_is_executable():
    import os
    from conftest import ROOT
    p = ROOT / "eringi-price"
    assert p.is_file() and os.access(p, os.X_OK)
    assert p.read_text().startswith("#!/usr/bin/env python3")
    assert sys.version_info >= (3, 9)
