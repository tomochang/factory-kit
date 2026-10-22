#!/usr/bin/env python3
"""ask-me: ask the owner a question in their own WhatsApp chat (via Beeper Desktop) and wait for the reply.

Usage: scripts/ask-me.py [--from LABEL] [--timeout SEC] "question"
       scripts/ask-me.py --wait Q7 [--timeout SEC]     # keep waiting on an open question, no resend
       scripts/ask-me.py [--from LABEL] --tell "text"  # send an update, don't wait
       scripts/ask-me.py [--from LABEL] --task "text"  # hand the factory foreman a task the owner asked for
       scripts/ask-me.py --check                       # check Beeper and the own chat, send nothing
Prints the reply on stdout and exits 0. Exit 3 = no reply before the timeout (rerun with --wait),
2 = refused (the chat is not the own chat, or bad arguments), 4 = the question closed unanswered
after a day, 1 = Beeper error.
Settings (chat, token file, Beeper base, state folder, owner name) come from factory.env; the real environment
wins (BEEPER_API_BASE, BEEPER_TOKEN_FILE, ASK_ME_CHAT, ASK_ME_STATE override it).
Only ever writes to the user's own "message yourself" chat; the check runs before every send.
A reply must start with the question's code ("Q7 yes"): the self chat also holds notes, forwards and
bridge notices, which are never taken as answers. Unanswered questions close after a day.
"""
from __future__ import annotations

import argparse
import fcntl
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import kitenv  # noqa: E402

CFG = kitenv.load()
BASE = CFG["BEEPER_BASE"]
TOKEN_FILE = Path(CFG["TOKEN_FILE"] or "/nonexistent/beeper-token")
CHAT = CFG["CHAT_ID"]
STATE = Path(CFG["ASK_ME_STATE"])
POLL = float(os.environ.get("ASK_ME_POLL", "10"))
MAX_AGE = 24 * 3600  # an unanswered question closes after a day
MARK = "🤖"  # every message this script sends starts with it, so it never reads its own messages as replies
CODE_RE = re.compile(r"^[\s\"'“”‘’「『]*q0*(\d+)\b[\s:.,、。-]*", re.I)


LOOPBACK = {"127.0.0.1", "localhost", "::1"}
OWNER_WORDS = {w for w in re.split(r"[^a-z0-9]+", CFG["OWNER_NAME"].lower()) if len(w) >= 2}


def check_base():
    """Beeper Desktop runs on this Mac; a base elsewhere could pass the own-chat check and post anywhere."""
    if not CHAT:
        sys.exit(f"ask-me: refused: no chat configured; set CHAT_ID in {CFG['ENV_FILE']} (see factory.env.example)")
    u = urllib.parse.urlsplit(BASE)
    if u.scheme != "http" or u.hostname not in LOOPBACK:
        sys.exit(f"ask-me: refused: BEEPER_API_BASE must be http://127.0.0.1 (Beeper on this Mac), not {BASE!r}")


def make_label(label):
    """A plain lowercase name. The default comes from the folder; an explicit one that isn't plain, or that
    looks like the owner, is refused: --task messages carry no mark, so the label must not pass as the owner."""
    if label is None:
        label = re.sub(r"[^a-z0-9-]+", "-", os.path.basename(os.getcwd()).lower()).strip("-")[:32] or "agent"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", label) or OWNER_WORDS & set(label.split("-")) \
            or any(len(w) >= 4 and w in label for w in OWNER_WORDS):
        sys.exit(f"ask-me: refused: --from must be a plain lowercase name like 'factory' and not the owner's, not {label!r}")
    return label


def clean_text(text):
    """Beeper returns API-sent messages as HTML (<p>, <br>, emoji <img>); compare and return plain text."""
    text = re.sub(r'<img[^>]*\balt="([^"]*)"[^>]*>', r"\1", text or "")
    text = re.sub(r"</p>\s*<p>", "\n\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {TOKEN_FILE.read_text().strip()}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def chat_path():
    return "/v1/chats/" + urllib.parse.quote(CHAT, safe="")


def check_own_chat():
    """The chat must be a 1:1 WhatsApp chat whose other side is the account's own phone number."""
    accounts = call("GET", "/v1/accounts")
    accounts = accounts.get("items", accounts) if isinstance(accounts, dict) else accounts
    chat = call("GET", chat_path())
    acct = next((a for a in accounts if a.get("accountID") == chat.get("accountID")), None)
    own = (acct or {}).get("user", {}).get("phoneNumber")
    others = [p for p in chat.get("participants", {}).get("items", []) if not p.get("isSelf")]
    if not (own and chat.get("type") == "single" and len(others) == 1 and others[0].get("phoneNumber") == own):
        sys.exit("ask-me: refused: the configured chat is not your own WhatsApp chat; nothing sent")


def send(text):
    check_own_chat()
    call("POST", chat_path() + "/messages", {"text": text})


def messages():
    d = call("GET", chat_path() + "/messages")
    items = d.get("items", d if isinstance(d, list) else [])
    return sorted(items, key=lambda m: int(m.get("sortKey") or 0))


class State:
    """Open questions in a small JSON file, guarded by flock (several agents may ask at once)."""

    def __init__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        self.path = STATE / "questions.json"
        self.lock = open(STATE / ".lock", "w")

    def __enter__(self):
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"next": 1, "questions": {}}
        return self.data

    def __exit__(self, *exc):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
        tmp.replace(self.path)
        fcntl.flock(self.lock, fcntl.LOCK_UN)


def ask(state, label, question):
    with state as d:
        code = f"Q{d['next']}"
        d["next"] += 1
        head = f"{MARK} {code}" + (f" · {label}" if label else "")
        after = max([int(m.get("sortKey") or 0) for m in messages()] or [0])  # before sending: a fast reply still counts
        # recorded before sending, so a reply to a sent question always has somewhere to go
        d["questions"][code] = {"question": question, "label": label, "after": after, "answer": None,
                                "asked_at": time.time()}
        try:
            send(f"{head}\n{question}\n\n(reply here starting with {code}, e.g. \"{code} yes\")")
        except OSError as e:  # it may still have been delivered, so the question stays open
            raise OSError(f"{e}; if it arrived anyway, wait with: scripts/ask-me.py --wait {code}") from e
    return code


def expire(d):
    for q in d["questions"].values():
        if q["answer"] is None and time.time() - q.get("asked_at", time.time()) > MAX_AGE:
            q["answer"], q["closed"] = "", True


def route(d, msgs):
    """Give each new message that starts with an open question's code to that question.
    Anything else in the self chat (notes, forwards, bridge notices) is not an answer; the first such
    message gets one reminder per open question."""
    expire(d)
    open_qs = {c: q for c, q in d["questions"].items() if q["answer"] is None}
    seen = d.setdefault("seen", 0)
    remind = []
    for m in msgs:
        key = int(m.get("sortKey") or 0)
        text = unicodedata.normalize("NFKC", clean_text(m.get("text")))  # full-width Ｑ１ from a Japanese IME
        if key <= seen or m.get("type", "TEXT") != "TEXT" or not text or text.startswith(MARK):
            continue
        d["seen"] = seen = key
        live = {c: q for c, q in open_qs.items() if q["after"] < key}
        hit = CODE_RE.match(text)
        code = f"Q{int(hit.group(1))}" if hit else None
        if code in live:
            d["questions"][code]["answer"] = text[hit.end():].strip()
            open_qs.pop(code)
            notify(f"{MARK} {code} received")
        elif code in d["questions"] and d["questions"][code].get("closed") and not d["questions"][code].get("told"):
            d["questions"][code]["told"] = True
            notify(f"{MARK} {code} was closed unanswered after a day; the agent will ask again if it still matters")
        else:
            remind += [c for c, q in live.items() if not q.get("reminded")]
    remind = sorted(set(remind) & set(open_qs), key=lambda c: int(c[1:]))
    if remind:
        for c in remind:
            d["questions"][c]["reminded"] = True
        notify(f"{MARK} To answer, start your reply with the question's code: " + " / ".join(remind))


def notify(text):
    """Acks and reminders are best effort: a Beeper hiccup must not kill a waiter whose answer is recorded."""
    try:
        send(text)
    except OSError as e:
        print(f"ask-me: could not send a notice: {e}", file=sys.stderr)


def wait(state, code, timeout):
    end = time.time() + timeout
    while True:
        with state as d:
            q = d["questions"].get(code)
            if q is None:
                sys.exit(f"ask-me: no question {code}")
            if q["answer"] is None:
                route(d, messages())
            if q.get("closed"):
                print(f"ask-me: {code} was closed unanswered after a day; ask again if it still matters", file=sys.stderr)
                sys.exit(4)
            if q["answer"] is not None:
                return q["answer"]
        if time.time() >= end:
            print(f"ask-me: no reply to {code} yet; keep waiting with: scripts/ask-me.py --wait {code}", file=sys.stderr)
            sys.exit(3)
        time.sleep(POLL)


def main():
    ap = argparse.ArgumentParser(description="Ask the owner in their own WhatsApp chat and wait for the reply.")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--from", dest="label", default=None)
    ap.add_argument("--timeout", type=float, default=3300)
    ap.add_argument("--wait", metavar="CODE")
    ap.add_argument("--tell", metavar="TEXT", help="send an update to the own chat and exit; no reply expected")
    ap.add_argument("--task", metavar="TEXT", help="hand a factory task to the foreman (an unmarked 'F:' message); "
                    "only when the owner asked for it")
    ap.add_argument("--check", action="store_true", help="check Beeper and that the chat is the own chat; send nothing")
    a = ap.parse_args()
    try:
        check_base()
        a.label = make_label(a.label)
    except SystemExit as e:
        print(e.code, file=sys.stderr)
        sys.exit(2)
    if a.check:
        try:
            check_own_chat()
        except SystemExit as e:
            print(e.code, file=sys.stderr)
            sys.exit(2)
        except OSError as e:
            print(f"ask-me: Beeper error: {e}", file=sys.stderr)
            sys.exit(1)
        print("ask-me: ok: Beeper answers and the configured chat is your own chat")
        return
    if a.task and re.match(r"\s*(f\s*[:：]\s*)?(go|stop)\b", unicodedata.normalize("NFKC", a.task), re.I):
        print("ask-me: --task never sends go or stop; only the owner starts or stops factory work", file=sys.stderr)
        sys.exit(2)
    if a.tell or a.task:
        text = (f"F: {a.task} (sent by {a.label or 'an agent'} at your request)" if a.task
                else f"{MARK} · {a.label}\n{a.tell}" if a.label else f"{MARK}\n{a.tell}")
        try:
            send(text)
        except SystemExit as e:
            print(e.code, file=sys.stderr)
            sys.exit(2)
        except OSError as e:
            print(f"ask-me: Beeper error: {e}", file=sys.stderr)
            sys.exit(1)
        return
    if bool(a.question) == bool(a.wait):
        ap.error("give a question, or --wait CODE")
    try:
        state = State()
        code = a.wait.upper() if a.wait else ask(state, a.label, a.question)
        if not a.wait:
            print(f"ask-me: sent {code}", file=sys.stderr)
        print(wait(state, code, a.timeout))
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            sys.exit(2)
        raise
    except OSError as e:
        print(f"ask-me: Beeper error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
