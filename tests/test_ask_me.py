"""ask-me.py (shipped test, generalised): ask the owner in their own WhatsApp chat via a fake Beeper."""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ask-me.py"
SELF = "!self:beeper.local"
ACCOUNT = "whatsapp.81700000000"


class Fake:
    """A tiny Beeper Desktop API: one self chat on one WhatsApp account."""

    def __init__(self, self_phone="+81700000000"):
        self.msgs = []  # newest last
        self.sent = []
        self.self_phone = self_phone
        self.lock = threading.Lock()

    def add(self, text, sender="@me:beeper.com"):
        with self.lock:
            self.msgs.append({"id": str(len(self.msgs) + 1), "sortKey": str(1000 + len(self.msgs)),
                              "text": text, "type": "TEXT", "isSender": True, "senderID": sender,
                              "timestamp": "2026-10-21T00:00:00Z"})

    def handler(fake):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/v1/accounts"):
                    return self.reply([{"accountID": ACCOUNT, "network": "WhatsApp",
                                        "user": {"phoneNumber": "+81700000000"}}])
                if self.path.endswith("/messages"):
                    with fake.lock:
                        return self.reply({"items": list(reversed(fake.msgs))})
                if self.path.startswith("/v1/chats/"):
                    return self.reply({"id": SELF, "accountID": ACCOUNT, "type": "single", "participants": {"items": [
                        {"id": "@wa:beeper.local", "phoneNumber": fake.self_phone, "isSelf": False},
                        {"id": "@me:beeper.com", "isSelf": True}]}})
                self.reply({}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                text = json.loads(self.rfile.read(n))["text"]
                fake.sent.append(text)
                fake.add("<p>" + text.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>")  # Beeper echoes HTML
                self.reply({"chatID": SELF, "pendingMessageID": "p1"})
        return H


def serve(fake):
    srv = HTTPServer(("127.0.0.1", 0), fake.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run(tmp_path, srv, *args, timeout=20, cwd=None, **extra):
    token = tmp_path / "token"
    token.write_text("t\n")
    env = {k: v for k, v in os.environ.items() if k not in ("OWNER_NAME", "CHAT_ID", "TOKEN_FILE", "BEEPER_BASE")}
    env.update(BEEPER_API_BASE=f"http://127.0.0.1:{srv.server_port}", BEEPER_TOKEN_FILE=str(token),
               ASK_ME_CHAT=SELF, ASK_ME_STATE=str(tmp_path / "state"), ASK_ME_POLL="0.2",
               FACTORY_ENV=str(tmp_path / "no-factory.env"), OWNER_NAME="Alex Example")
    env.update(extra)
    return subprocess.Popen([sys.executable, str(SCRIPT), *args], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=cwd)


def wait_sent(fake, n=1):
    for _ in range(100):
        if len(fake.sent) >= n:
            return
        time.sleep(0.05)
    raise AssertionError(f"only {len(fake.sent)} sent")


def test_asks_and_returns_the_reply_typed_on_the_phone(tmp_path):
    fake = Fake()
    fake.add("old note to self")
    srv = serve(fake)
    p = run(tmp_path, srv, "--from", "myproj", "Ship it now or wait?")
    wait_sent(fake)
    assert "Ship it now or wait?" in fake.sent[0] and "myproj" in fake.sent[0]
    code = fake.sent[0].split()[1]
    fake.add("Incoming call. Use the WhatsApp app to answer.")  # bridge notices and notes are not answers
    fake.add("just a note to self")
    time.sleep(1)
    assert p.poll() is None
    assert sum("start your reply" in s and code in s for s in fake.sent[1:]) == 1  # one reminder, not one per note
    fake.add(f"{code} wait until tomorrow")
    out, err = p.communicate(timeout=10)
    assert p.returncode == 0, err
    assert out.strip() == "wait until tomorrow"
    assert any("received" in s.lower() for s in fake.sent[1:])  # short ack on the phone


def test_two_open_questions_need_the_code_and_each_gets_its_own_answer(tmp_path):
    fake = Fake()
    srv = serve(fake)
    a = run(tmp_path, srv, "question A")
    wait_sent(fake, 1)
    b = run(tmp_path, srv, "question B")
    wait_sent(fake, 2)
    code_a = fake.sent[0].split()[1]
    code_b = fake.sent[1].split()[1]
    assert code_a != code_b and code_a.startswith("Q")
    fake.add("no code, ambiguous")
    time.sleep(1)
    assert a.poll() is None and b.poll() is None  # nobody takes a reply without a code
    fake.add(f"{code_b.lower()} answer for B")
    fake.add(f"{code_a}: answer for A")
    out_a, _ = a.communicate(timeout=10)
    out_b, _ = b.communicate(timeout=10)
    assert out_a.strip() == "answer for A" and out_b.strip() == "answer for B"


def test_refuses_a_chat_that_is_not_the_users_own(tmp_path):
    fake = Fake(self_phone="+819999999999")
    srv = serve(fake)
    p = run(tmp_path, srv, "hello?")
    out, err = p.communicate(timeout=10)
    assert p.returncode == 2 and fake.sent == []
    assert "own" in err


def test_timeout_then_wait_resumes_without_resending(tmp_path):
    fake = Fake()
    srv = serve(fake)
    p = run(tmp_path, srv, "--timeout", "1", "slow question")
    out, err = p.communicate(timeout=10)
    assert p.returncode == 3
    code = fake.sent[0].split()[1]
    assert code in err
    q = run(tmp_path, srv, "--wait", code)
    time.sleep(0.5)
    fake.add(f"{code} late answer")
    out, err = q.communicate(timeout=10)
    assert q.returncode == 0 and out.strip() == "late answer"
    assert sum("slow question" in s for s in fake.sent) == 1


def test_questions_older_than_a_day_are_closed(tmp_path):
    fake = Fake()
    srv = serve(fake)
    state = tmp_path / "state"
    state.mkdir()
    (state / "questions.json").write_text(json.dumps({"next": 5, "seen": 0, "questions": {
        "Q4": {"question": "stale", "label": "x", "after": 0, "answer": None, "asked_at": time.time() - 90000}}}))
    p = run(tmp_path, srv, "fresh question")
    wait_sent(fake)
    fake.add("Q5 yes")
    out, err = p.communicate(timeout=10)
    assert out.strip() == "yes"
    q = run(tmp_path, srv, "--wait", "Q4", "--timeout", "1")
    _, err = q.communicate(timeout=10)
    assert q.returncode == 4 and "closed" in err


def test_phone_style_codes_and_non_text_are_handled(tmp_path):
    fake = Fake()
    srv = serve(fake)
    p = run(tmp_path, srv, "pick one")
    wait_sent(fake)
    code = fake.sent[0].split()[1]
    with fake.lock:
        fake.msgs.append({"id": "x", "sortKey": str(1000 + len(fake.msgs)), "text": f"{code} from a reaction", "type": "REACTION"})
    fake.add("\uff31" + chr(0xff10 + int(code[1:])) + "\u3000\u306f\u3044")  # full-width "Q1 はい" from a Japanese IME
    out, err = p.communicate(timeout=10)
    assert p.returncode == 0, err
    assert out.strip() == "\u306f\u3044"


def test_a_late_reply_to_a_closed_question_is_told_so(tmp_path):
    fake = Fake()
    srv = serve(fake)
    state = tmp_path / "state"
    state.mkdir()
    (state / "questions.json").write_text(json.dumps({"next": 5, "seen": 0, "questions": {
        "Q4": {"question": "stale", "label": "x", "after": 0, "answer": "", "closed": True, "asked_at": 0}}}))
    p = run(tmp_path, srv, "fresh")
    wait_sent(fake)
    fake.add("Q4 yes")
    time.sleep(1)
    fake.add("Q5 ok")
    out, _ = p.communicate(timeout=10)
    assert out.strip() == "ok"
    assert sum("Q4 was closed" in s for s in fake.sent) == 1


def test_tell_sends_without_waiting_and_only_to_the_own_chat(tmp_path):
    fake = Fake()
    srv = serve(fake)
    p = run(tmp_path, srv, "--from", "factory", "--tell", "Round 1 done; 25 sources saved.")
    out, err = p.communicate(timeout=10)
    assert p.returncode == 0, err
    assert fake.sent == ["🤖 · factory\nRound 1 done; 25 sources saved."]
    other = Fake(self_phone="+819999999999")
    q = run(tmp_path, serve(other), "--tell", "hi")
    q.communicate(timeout=10)
    assert q.returncode == 2 and other.sent == []


def test_task_sends_an_unmarked_factory_task_labelled_with_its_sender(tmp_path):
    fake = Fake()
    srv = serve(fake)
    p = run(tmp_path, srv, "--from", "claude", "--task", "new repo ~/factory/x: a tiny CLI")
    out, err = p.communicate(timeout=10)
    assert p.returncode == 0, err
    assert fake.sent == ["F: new repo ~/factory/x: a tiny CLI (sent by claude at your request)"]
    other = Fake(self_phone="+819999999999")
    q = run(tmp_path, serve(other), "--task", "x")
    q.communicate(timeout=10)
    assert q.returncode == 2 and other.sent == []


def test_task_refuses_go_and_stop_so_an_agent_cannot_start_its_own_work(tmp_path):
    fake = Fake()
    srv = serve(fake)
    for text in ["go T3", "  GO T1,T2", "F: go T3", "stop T2", "ｇｏ T4"]:
        p = run(tmp_path, srv, "--from", "claude", "--task", text)
        p.communicate(timeout=10)
        assert p.returncode == 2, text
    assert fake.sent == []
    p = run(tmp_path, srv, "--from", "claude", "--task", "good first issue: add a --help flag")
    p.communicate(timeout=10)
    assert p.returncode == 0 and len(fake.sent) == 1


def test_a_beeper_base_off_this_mac_is_refused_before_anything_is_sent(tmp_path):
    fake = Fake()
    srv = serve(fake)
    for base in ["http://example.com:23373", "http://10.0.0.5:23373", "https://127.0.0.1.evil.test"]:
        p = run(tmp_path, srv, "--tell", "hi", BEEPER_API_BASE=base)
        out, err = p.communicate(timeout=10)
        assert p.returncode == 2 and "127.0.0.1" in err, (base, err)
    assert fake.sent == []


def test_a_label_that_looks_like_the_owner_or_is_not_a_plain_name_is_refused(tmp_path):
    fake = Fake()
    srv = serve(fake)
    for label in ["Alex", "example", "alex-san", "EXAMPLE", "two words", "a" * 40]:
        p = run(tmp_path, srv, "--from", label, "--task", "new repo ~/factory/x")
        p.communicate(timeout=10)
        assert p.returncode == 2, label
    assert fake.sent == []
    d = tmp_path / "My Project.v2"
    d.mkdir()
    p = run(tmp_path, srv, "--tell", "hi", cwd=d)
    out, err = p.communicate(timeout=10)
    assert p.returncode == 0, err
    assert fake.sent == ["🤖 · my-project-v2\nhi"]


# --- kit additions: config from factory.env, --check for doctor.sh, a clear error without a chat


def test_check_verifies_the_own_chat_without_sending(tmp_path):
    fake = Fake()
    srv = serve(fake)
    p = run(tmp_path, srv, "--check")
    out, err = p.communicate(timeout=10)
    assert p.returncode == 0, err
    assert "own chat" in out and fake.sent == []
    other = Fake(self_phone="+819999999999")
    q = run(tmp_path, serve(other), "--check")
    q.communicate(timeout=10)
    assert q.returncode == 2 and other.sent == []


def test_check_reports_beeper_down(tmp_path):
    fake = Fake()
    srv = serve(fake)
    port = srv.server_port
    srv.shutdown()
    srv.server_close()
    p = run(tmp_path, srv, "--check", BEEPER_API_BASE=f"http://127.0.0.1:{port}")
    out, err = p.communicate(timeout=20)
    assert p.returncode == 1 and "Beeper" in err


def test_values_come_from_factory_env(tmp_path):
    fake = Fake()
    srv = serve(fake)
    token = tmp_path / "tok"
    token.write_text("t\n")
    envfile = tmp_path / "factory.env"
    envfile.write_text(f'OWNER_NAME="Alex Example"\nCHAT_ID="{SELF}"\nTOKEN_FILE="{token}"\n'
                       f'BEEPER_BASE="http://127.0.0.1:{srv.server_port}"\nASK_ME_STATE="{tmp_path}/st"\n')
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BEEPER_", "ASK_ME_")) and k not in ("OWNER_NAME", "CHAT_ID", "TOKEN_FILE")}
    env["FACTORY_ENV"] = str(envfile)
    r = subprocess.run([sys.executable, str(SCRIPT), "--from", "factory", "--tell", "hello"], env=env,
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    assert fake.sent == ["🤖 · factory\nhello"]


def test_no_chat_configured_is_refused_with_the_file_name(tmp_path):
    fake = Fake()
    srv = serve(fake)
    p = run(tmp_path, srv, "--tell", "hi", ASK_ME_CHAT="")
    out, err = p.communicate(timeout=10)
    assert p.returncode == 2 and "CHAT_ID" in err and "factory.env" in err
    assert fake.sent == []
