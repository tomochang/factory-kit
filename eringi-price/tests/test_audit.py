"""R8 audit: KGI and K1-K5 from the run log and the stored raw files.

KGI check: the KGI itself (27 of 28 days with exactly one correct report by 17:30, 0 stale-as-new days) can
only be measured after delivery, over his 28-day trial: run `eringi-price audit` after the trial (ends by
2026-12-15). Here the same computation runs on a planted 28-day log."""
import io
import json
from datetime import timedelta

from conftest import at, fetcher_for, seed_months, world_2026_10_25

from eringi_price import audit, cli


def sent_run(config, store, tmp_path, now):
    stub = tmp_path / "ask-me-stub.py"
    stub.write_text("import sys\n")
    config.ask_me = str(stub)
    f, _ = fetcher_for(config, world_2026_10_25())
    assert cli.main(["run"], config=config, fetcher=f, now=now, out=io.StringIO()) == 0


def test_clean_run_meets_k1_to_k4(config, store, tmp_path):
    seed_months(store, ["202509", "202608", "202609"])
    sent_run(config, store, tmp_path, at(2026, 10, 25))
    r = audit.audit(store)
    assert r["K1"]["checked"] >= 8 and r["K1"]["mismatch"] == [] and r["K1"]["ok"]
    assert r["K2"]["stale_as_new"] == 0 and r["K2"]["unreported"] == [] and r["K2"]["ok"]
    assert r["K3"]["min_cover"] >= 95.0 and r["K3"]["ok"]
    assert r["K4"]["bad"] == [] and r["K4"]["ok"]


def test_tampered_raw_file_is_caught(config, store, tmp_path):
    seed_months(store, ["202509", "202608", "202609"])
    sent_run(config, store, tmp_path, at(2026, 10, 25))
    p = store.tokyo_path("202609")
    p.write_bytes(p.read_bytes()[:-10] + b"0123456789")
    r = audit.audit(store)
    assert not r["K1"]["ok"] and any("sha256" in m for m in r["K1"]["mismatch"])


def test_wrong_figure_in_the_message_is_caught(config, store, tmp_path):
    seed_months(store, ["202509", "202608", "202609"])
    sent_run(config, store, tmp_path, at(2026, 10, 25))
    runs = store.runs()
    runs[0]["message"] = runs[0]["message"].replace("平均613 ", "平均612 ")
    store.runs_path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in runs))
    r = audit.audit(store)
    assert not r["K1"]["ok"]


def test_stale_shown_as_new_is_counted(config, store, tmp_path):
    seed_months(store, ["202509", "202608", "202609"])
    sent_run(config, store, tmp_path, at(2026, 10, 25))
    runs = store.runs()
    runs[0]["figures"].append({"key": "osaka:本場:新潟:2026-10-24:mid", "value": 1000, "text": "新潟1,000",
                               "as_of": "2026-10-24", "today": True})
    runs[0]["message"] += "\n大阪本場(中値) 新潟1,000"
    store.runs_path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in runs))
    assert audit.audit(store)["K2"]["stale_as_new"] == 1


def test_unreported_closed_day_is_caught(config, store, tmp_path):
    seed_months(store, ["202509", "202608", "202609"])
    sent_run(config, store, tmp_path, at(2026, 10, 25))
    runs = store.runs()
    runs[0]["message"] = runs[0]["message"].replace("は休市", "")
    store.runs_path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in runs))
    r = audit.audit(store)
    assert r["K2"]["unreported"] and not r["K2"]["ok"]


def planted_trial(store, days=28, missing=(), late=(), twice=()):
    start = at(2026, 11, 16)
    for i in range(days):
        if i in missing:
            continue
        t = start + timedelta(days=i)
        if i in late:
            t = t.replace(hour=17, minute=45)
        for _ in range(2 if i in twice else 1):
            store.append_run({"day": t.date().isoformat(), "run_at": t.isoformat(), "sent_at": t.isoformat(),
                              "dry_run": False, "message": "Nothing needed - test", "day_status": "open",
                              "problems": [], "sources": {}, "figures": []})


def test_kgi_counts_days_with_exactly_one_report_by_17_30(store):
    planted_trial(store, missing=(3,), late=(10,), twice=(20,))
    k = audit.audit(store)["KGI"]
    assert (k["good_days"], k["days"]) == (25, 28)
    assert k["target_met"] is False
    store.runs_path.unlink()
    planted_trial(store, missing=(3,))
    k = audit.audit(store)["KGI"]
    assert (k["good_days"], k["days"], k["target_met"]) == (27, 28, True)


def test_k5_reads_the_canary_log(config, store):
    cli.main(["canary"], config=config, out=io.StringIO())
    r = audit.audit(store)
    assert r["K5"]["runs"] == 1 and r["K5"]["ok"]


def test_format_audit_is_phone_short(config, store, tmp_path):
    seed_months(store, ["202509", "202608", "202609"])
    sent_run(config, store, tmp_path, at(2026, 10, 25))
    text = audit.format_audit(audit.audit(store))
    assert text.startswith("Nothing needed - ") and len(text) <= 700 and len(text.split("\n")) <= 10
