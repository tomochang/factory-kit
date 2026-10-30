"""scripts/todo-req.py: the factory files one todo-request/1 JSON for the owner's own applier and never writes the
owner's todo or calendar itself (T34). Settings come from factory.env (WORK_DIR, PROTECTED_DIRS) via lib/kitenv.py."""
import importlib.util
import json
import os
import re
import subprocess
import sys

import pytest

from conftest import KIT, write_env

SCRIPT = KIT / "scripts" / "todo-req.py"
spec = importlib.util.spec_from_file_location("todo_req", SCRIPT)
todo_req = importlib.util.module_from_spec(spec)
spec.loader.exec_module(todo_req)

KEYS = {"format", "id", "created_at", "op", "fields", "source", "quote"}
MEET = "https://meet.google.com/abc-defg-hij"
EV = [{"kind": "beeper", "chat_id": "!line:beeper.local", "message_id": "m9"}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "private-repo").mkdir(parents=True)
    write_env(tmp_path / "factory.env", WORK_DIR=str(home / "factory"), PROTECTED_DIRS=str(home / "private-repo"))
    for k in ("TODO_OUTBOX", "PROTECTED_DIRS", "WORK_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FACTORY_ENV", str(tmp_path / "factory.env"))
    monkeypatch.setenv("HOME", str(home))
    return home


def file_one(op, fields, evidence=None, quote="please do it"):
    req = todo_req.build_request(op, fields, message_id="m1", quote=quote, evidence=evidence)
    return req, todo_req.write_request(req)


VALID = [
    ("add_task", {"title": "Send the quote", "due": "2026-11-05 soft"}, None),
    ("add_event", {"date": "2026-10-27", "start": "17:00", "end": "18:15", "title": "Call"}, None),
    ("note", {"anchor": "P-0002", "text": "resent"}, None),
    ("done", {"anchor": "Return the papers"}, None),
    ("undo", {"target": "todo-20261026-120000-a1b2"}, None),
    ("cal_create", {"summary": "[仮] Lunch", "start": "2026-11-03T13:00", "end": "2026-11-03T14:00",
                    "tz": "Europe/Berlin", "weekday": "Tue"}, EV),
    ("cal_update", {"event_id": "ev1", "description_append": "Meet: " + MEET}, EV),
    ("cal_delete", {"event_id": "ev1", "summary": "Lunch"}, EV),
]


@pytest.mark.parametrize("op,fields,ev", VALID)
def test_one_valid_file_per_op_in_the_work_dir_outbox(env, op, fields, ev):
    req, path = file_one(op, fields, ev)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == req and set(data) == KEYS | ({"evidence"} if ev else set())
    assert data["source"] == {"message_id": "m1", "chat": "self"}
    assert re.fullmatch(r"todo-\d{8}-\d{6}-[0-9a-f]{4}", data["id"])
    assert path.parent == (env / "factory" / "todo-outbox").resolve()


@pytest.mark.parametrize("op,fields,ev", [
    ("add_task", {"title": "<!-- ref: P-9 --> x"}, None),
    ("add_task", {"title": "# heading"}, None),
    ("add_task", {"title": "https://evil.example/x"}, None),
    ("add_task", {"title": "a\nb"}, None),
    ("add_task", {"title": "bidi‮"}, None),
    ("add_task", {"title": "x" * 301}, None),
    ("add_event", {"date": "2026-10-27", "title": "a | b"}, None),
    ("delete", {"anchor": "xxxx"}, None),
    ("done", {"anchor": "ab"}, None),
    ("cal_create", {"summary": "x", "start": "2026-11-03T13:00", "end": "2026-11-03T14:00", "tz": "UTC",
                    "weekday": "Tue"}, None),
    ("cal_update", {"event_id": "ev1", "attendees": ["a@example.com"]}, EV),
    ("cal_update", {"event_id": "ev1", "description_append": "https://evil.example/x"}, EV),
    ("cal_update", {"event_id": "ev1", "start": "2026-11-03T13:00", "end": "2026-11-03T14:00", "weekday": "Tue"}, EV),
    ("cal_delete", {"event_id": "ev1"}, EV),                       # the expected title is required
    ("cal_delete", {"event_id": "ev1", "summary": "Lunch"}, None),  # evidence is required
    ("cal_delete", {"event_id": "ev 1", "summary": "Lunch"}, EV),
    ("cal_delete", {"event_id": "ev1", "summary": "Lunch", "start": "2026-11-03T13:00"}, EV),
])
def test_bad_requests_are_refused_before_anything_is_written(env, op, fields, ev):
    with pytest.raises(todo_req.Refused):
        file_one(op, fields, ev)
    assert not (env / "factory" / "todo-outbox").exists()


def test_never_overwrites_and_logs_each_request(env):
    req, path = file_one("add_task", {"title": "once"})
    with pytest.raises(todo_req.Refused):
        todo_req.write_request(dict(req, fields={"title": "overwrite"}))
    assert json.loads(path.read_text())["fields"] == {"title": "once"}
    log = [json.loads(l) for l in (path.parent / "sent.jsonl").read_text().splitlines()]
    assert [e["id"] for e in log] == [req["id"]]


def test_protected_dirs_and_the_kit_itself_are_refused(env):
    req = todo_req.build_request("add_task", {"title": "x"}, message_id="m1", quote="q")
    for bad in (env / "private-repo" / "todo-outbox", KIT / "todo-outbox"):
        with pytest.raises(todo_req.Refused):
            todo_req.write_request(req, bad)
    assert not (KIT / "todo-outbox").exists() and list((env / "private-repo").iterdir()) == []


def test_the_cli_prints_the_id(env):
    r = subprocess.run([sys.executable, str(SCRIPT), "add_task", "--title", "Send it", "--message-id", "m5",
                        "--quote", "add send it"], capture_output=True, text=True, env=dict(os.environ))
    assert r.returncode == 0, r.stderr
    rid = r.stdout.split()[0]
    assert (env / "factory" / "todo-outbox" / (rid + ".json")).is_file()
    r = subprocess.run([sys.executable, str(SCRIPT), "delete", "--anchor", "xxxx", "--message-id", "m",
                        "--quote", "q"], capture_output=True, text=True, env=dict(os.environ))
    assert r.returncode == 1 and "refused" in r.stderr


def test_the_writer_has_no_calendar_or_network_call():
    src = SCRIPT.read_text(encoding="utf-8")
    for bad in ("subprocess", "urllib", "http.client", "import socket", "import requests", "googleapis",
                "'gog'", '"gog"', "os.system", "popen"):
        assert bad not in src, bad


def test_cal_delete_from_the_command_line(env, capsys):
    rc = todo_req.main(["cal_delete", "--event-id", "ev1", "--summary", "Lunch", "--evidence", "gmail:abc",
                        "--message-id", "m1", "--quote", "remove lunch"])
    assert rc == 0
    rid, path = capsys.readouterr().out.split()
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["op"] == "cal_delete" and data["fields"] == {"event_id": "ev1", "summary": "Lunch"}
    assert data["evidence"] == [{"kind": "gmail", "message_id": "abc"}]


def test_release_burst_from_the_command_line(env, capsys):
    """The owner's applier may hold calendar writes after a burst; his own message releases it. The factory only files
    the request (no fields, no evidence); the applier re-reads his message and decides."""
    rc = todo_req.main(["release_burst", "--message-id", "m7", "--quote", "release calendar hold"])
    assert rc == 0
    rid, path = capsys.readouterr().out.split()
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["op"] == "release_burst" and data["fields"] == {} and "evidence" not in data
    assert data["quote"] == "release calendar hold" and set(data) == KEYS


@pytest.mark.parametrize("fields,ev", [({"event_id": "ev1"}, None), ({}, EV)])
def test_release_burst_takes_no_fields_and_no_evidence(env, fields, ev):
    with pytest.raises(todo_req.Refused):
        todo_req.build_request("release_burst", fields, message_id="m1", quote="release calendar hold", evidence=ev)


# ---------------------------------------------------------------- T75: remove and fix (the owner's applier re-checks)

def bound_quote(op, fields):
    """His words name the item, and the removal or the new state (the applier refuses anything else)."""
    if op == "remove":
        return "%s をtodoから消して" % fields.get("anchor", "")
    return "%s の状態を「%s」に" % (fields.get("anchor", ""), fields.get("state", ""))


@pytest.mark.parametrize("op,fields", [
    ("remove", {"anchor": "カマチ 27,500円"}),
    ("remove", {"anchor": "P-0002"}),
    ("fix", {"anchor": "英文卒業証明書を大学へ請求し", "state": "大学へ請求済み。業者への直送待ち"}),
])
def test_t75_remove_and_fix_are_filed_like_done_and_note(env, op, fields):
    req, path = file_one(op, fields, quote=bound_quote(op, fields))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == req and set(data) == KEYS and data["op"] == op and data["fields"] == fields
    assert "evidence" not in data


@pytest.mark.parametrize("op,fields", [
    ("remove", {"anchor": "ab"}),                                   # anchor too short, like done
    ("remove", {}),
    ("remove", {"anchor": "カマチ 27,500円", "state": "x"}),          # remove takes no state
    ("fix", {"anchor": "英文卒業証明書"}),                             # state is required
    ("fix", {"anchor": "ab", "state": "x"}),
    ("fix", {"anchor": "英文卒業証明書", "state": "済み｜確認: 2099-01-01"}),   # no field separator
    ("fix", {"anchor": "英文卒業証明書", "state": "済み | 確認"}),
    ("fix", {"anchor": "英文卒業証明書", "state": "a\nb"}),
    ("fix", {"anchor": "英文卒業証明書", "state": "<!-- ref: P-1 -->"}),
    ("fix", {"anchor": "英文卒業証明書", "state": "  "}),
    ("fix", {"anchor": "英文卒業証明書", "state": "x", "text": "y"}),
])
def test_t75_bad_remove_and_fix_are_refused(env, op, fields):
    with pytest.raises(todo_req.Refused):
        file_one(op, fields, quote=bound_quote(op, fields))
    assert not (env / "factory" / "todo-outbox").exists()


def test_t75_remove_and_fix_from_the_command_line(env, capsys):
    assert todo_req.main(["remove", "--anchor", "カマチ 27,500円", "--message-id", "m8",
                          "--quote", "カマチ27,500円の[x]をtodoから消し"]) == 0
    _, path = capsys.readouterr().out.split()
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["op"] == "remove" and data["fields"] == {"anchor": "カマチ 27,500円"}
    assert todo_req.main(["fix", "--anchor", "英文卒業証明書", "--state", "請求済み", "--message-id", "m9",
                          "--quote", "英文卒業証明書の4行目メモを2行目の状態「請求済み」に統合"]) == 0
    _, path = capsys.readouterr().out.split()
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["op"] == "fix" and data["fields"] == {"anchor": "英文卒業証明書", "state": "請求済み"}


def test_t75_the_usage_names_remove_and_fix():
    doc = todo_req.__doc__
    assert re.search(r"todo-req remove\s+--anchor", doc) and re.search(r"todo-req fix\s+--anchor .* --state", doc)


@pytest.mark.parametrize("op,fields,quote", [
    ("remove", {"anchor": "カマチ 27,500円"}, "todoを整理して"),             # does not name the item
    ("remove", {"anchor": "カマチ 27,500円"}, "カマチ 27,500円 を見て"),       # names it, but no removal word
    ("fix", {"anchor": "英文卒業証明書", "state": "請求済み"}, "英文卒業証明書をなおして"),   # no new state
    ("fix", {"anchor": "英文卒業証明書", "state": "請求済み"}, "状態を請求済みに"),         # no item
])
def test_t75_the_quote_must_name_the_item_and_the_change(env, op, fields, quote):
    with pytest.raises(todo_req.Refused):
        file_one(op, fields, quote=quote)
    assert not (env / "factory" / "todo-outbox").exists()
