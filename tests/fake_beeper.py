#!/usr/bin/env python3
"""A tiny fake Beeper Desktop API for the kit's tests: one WhatsApp account and its "message yourself" chat.

Never talks to the real Beeper. As a library: Fake(), serve(fake). As a program (for shell tests):
    fake_beeper.py --port-file F [--self-phone +15550000001]
serves until killed. Extra test endpoints: POST /test/add {"text": ...} adds a message the owner typed,
GET /test/sent lists what the kit sent.
"""
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SELF = "!self:beeper.local"
ACCOUNT = "whatsapp.15550000000"
OWN_PHONE = "+15550000000"


class Fake:
    def __init__(self, self_phone=OWN_PHONE):
        self.msgs = []
        self.sent = []
        self.self_phone = self_phone
        self.lock = threading.Lock()
        self.auth = []

    def add(self, text, kind="TEXT"):
        with self.lock:
            self.msgs.append({"id": str(len(self.msgs) + 1), "sortKey": str(1000 + len(self.msgs)), "text": text,
                              "type": kind, "isSender": True, "timestamp": "2026-10-21T00:00:00Z"})

    def handler(fake):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def body(self):
                n = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                fake.auth.append(self.headers.get("Authorization"))
                if self.path == "/test/sent":
                    return self.reply(fake.sent)
                if self.path.startswith("/v1/accounts"):
                    return self.reply([{"accountID": ACCOUNT, "network": "WhatsApp",
                                        "user": {"phoneNumber": OWN_PHONE}}])
                if self.path.endswith("/messages"):
                    with fake.lock:
                        return self.reply({"items": list(reversed(fake.msgs))})
                if self.path.startswith("/v1/chats/"):
                    return self.reply({"id": SELF, "accountID": ACCOUNT, "type": "single", "participants": {"items": [
                        {"id": "@wa:beeper.local", "phoneNumber": fake.self_phone, "isSelf": False},
                        {"id": "@me:beeper.com", "isSelf": True}]}})
                self.reply({}, 404)

            def do_POST(self):
                data = self.body()
                if self.path == "/test/add":
                    fake.add(data["text"])
                    return self.reply({"ok": True})
                text = data["text"]
                fake.sent.append(text)
                fake.add("<p>" + text.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>")
                self.reply({"chatID": SELF, "pendingMessageID": "p1"})
        return H


def serve(fake):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port-file", required=True)
    ap.add_argument("--self-phone", default=OWN_PHONE)
    a = ap.parse_args()
    fake = Fake(a.self_phone)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    with open(a.port_file + ".tmp", "w") as f:
        f.write(str(srv.server_port))
    import os
    os.replace(a.port_file + ".tmp", a.port_file)
    srv.serve_forever()


if __name__ == "__main__":
    main()
