"""T72: todo-req --source terminal. A todo change he asks for in a terminal session (no WhatsApp message) is filed
with his exact words; the owner's applier sends it to the phone-approval page and writes only after his passkey
permit. Only add_task, note and done; calendar ops keep needing WhatsApp provenance."""
import importlib.util
import json

import pytest

from conftest import KIT, write_env

SCRIPT = KIT / "scripts" / "todo-req.py"
spec = importlib.util.spec_from_file_location("todo_req_t72", SCRIPT)
todo_req = importlib.util.module_from_spec(spec)
spec.loader.exec_module(todo_req)

KEYS = {"format", "id", "created_at", "op", "fields", "source", "quote"}
LINE_EV = [{"kind": "beeper", "chat_id": "!line:beeper.local", "message_id": "m6124"}]


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


def terminal(op, fields, evidence=None, quote="これをdoneにして"):
    req = todo_req.build_request(op, fields, message_id=None, quote=quote, evidence=evidence, source="terminal")
    return req, todo_req.write_request(req)


@pytest.mark.parametrize("op,fields,ev", [
    ("add_task", {"title": "Send the quote"}, None),
    ("note", {"anchor": "P-0002", "text": "resent"}, LINE_EV),
    ("done", {"anchor": "Matsuike limit reply"}, LINE_EV),
    ("done", {"anchor": "Yamagishi introduction"}, LINE_EV * 1 + [
        {"kind": "beeper", "chat_id": "!oth:beeper.local", "message_id": "2745"}]),
    ("done", {"anchor": "Return the papers"}, None),
])
def test_terminal_todo_ops_are_filed_with_his_words_and_no_message_id(env, op, fields, ev):
    req, path = terminal(op, fields, ev)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == req
    assert data["source"] == {"kind": "terminal"}
    assert data["quote"] == "これをdoneにして"
    assert set(data) == KEYS | ({"evidence"} if ev else set())
    if ev:
        assert data["evidence"] == ev
    log = [json.loads(l) for l in (path.parent / "sent.jsonl").read_text().splitlines()]
    assert log[-1]["id"] == req["id"] and log[-1]["source"] == "terminal"


@pytest.mark.parametrize("op,fields,ev", [
    ("add_event", {"date": "2026-10-27", "title": "Call"}, None),
    ("undo", {"target": "todo-20261026-120000-a1b2"}, None),
    ("release_burst", {}, None),
    ("cal_create", {"summary": "Lunch", "start": "2026-11-03T13:00", "end": "2026-11-03T14:00",
                    "tz": "Asia/Tokyo", "weekday": "Tue"}, LINE_EV),
    ("cal_update", {"event_id": "ev1", "location": "Tokyo"}, LINE_EV),
    ("cal_delete", {"event_id": "ev1", "summary": "Lunch"}, LINE_EV),
    ("done", {"anchor": "Return the papers"}, [{"kind": "gmail", "message_id": "abc"}]),   # Beeper only
    ("done", {"anchor": "Return the papers"}, LINE_EV * 6),                               # at most 5
    ("done", {"anchor": "ab"}, None),
    ("add_task", {"title": "https://evil.example/x"}, None),
])
def test_terminal_refuses_everything_but_add_task_note_done(env, op, fields, ev):
    with pytest.raises(todo_req.Refused):
        terminal(op, fields, ev)
    assert not (env / "factory" / "todo-outbox").exists()


def test_terminal_with_a_message_id_or_a_chat_is_refused(env):
    with pytest.raises(todo_req.Refused):
        todo_req.build_request("done", {"anchor": "Return the papers"}, message_id="m1", quote="q",
                               source="terminal")
    with pytest.raises(todo_req.Refused):
        todo_req.build_request("done", {"anchor": "Return the papers"}, message_id=None, quote="q",
                               chat="!x:beeper.local", source="terminal")
    with pytest.raises(todo_req.Refused):
        todo_req.build_request("done", {"anchor": "Return the papers"}, message_id=None, quote="q",
                               source="email")


def test_whatsapp_stays_as_before(env):
    req = todo_req.build_request("done", {"anchor": "Return the papers"}, message_id="m1", quote="q")
    assert req["source"] == {"message_id": "m1", "chat": "self"}
    with pytest.raises(todo_req.Refused):      # WhatsApp still needs the message id
        todo_req.build_request("done", {"anchor": "Return the papers"}, message_id=None, quote="q")
    with pytest.raises(todo_req.Refused):      # evidence on a WhatsApp todo op is still refused
        todo_req.build_request("done", {"anchor": "Return the papers"}, message_id="m1", quote="q", evidence=LINE_EV)


def test_terminal_from_the_command_line(env, capsys):
    rc = todo_req.main(["done", "--anchor", "Matsuike limit reply", "--source", "terminal",
                        "--quote", "これをできないのも修正して", "--evidence", "beeper:!line:beeper.local:m6124"])
    assert rc == 0
    out = capsys.readouterr()
    rid, path = out.out.split()
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["source"] == {"kind": "terminal"} and data["evidence"] == LINE_EV
    assert "phone-approval" in out.err      # the foreman learns the request waits for his passkey
    assert todo_req.main(["done", "--anchor", "Matsuike limit reply", "--source", "terminal", "--message-id", "m1",
                          "--quote", "q"]) == 1
    assert todo_req.main(["done", "--anchor", "Matsuike limit reply", "--quote", "q"]) == 1   # WhatsApp needs an id
