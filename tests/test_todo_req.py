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
