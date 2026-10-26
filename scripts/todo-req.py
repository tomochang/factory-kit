#!/usr/bin/env python3
"""todo-req: file one request to change the owner's todo or the owner's own calendar (format todo-request/1).

The factory never writes the owner's todo or calendar. It drops one JSON file per request into the outbox
(default <WORK_DIR>/todo-outbox/); the owner's own applier, outside the factory, re-checks everything,
re-reads the owner's WhatsApp message and the evidence, applies it and reports to the owner. This script only writes that
one file (atomically, never overwriting) and appends one line to <outbox>/sent.jsonl.

Usage (every request needs the message id from the channel and the owner's exact words):
  todo-req add_task   --title T [--due D] [--status S]                      --message-id ID --quote Q
  todo-req add_event  --date YYYY-MM-DD [--start HH:MM] [--end HH:MM] [--tz IANA] --title T [--note N] ...
  todo-req note       --anchor P-xxxx|exact text --text T                    ...
  todo-req done       --anchor P-xxxx|exact text                             ...
  todo-req undo       --target <request id>                                 ...
  todo-req cal_create --summary S --start YYYY-MM-DDTHH:MM --end ... --tz IANA --weekday 火|Tue
                      [--all-day] [--location L] [--description D] --evidence beeper:<chat>:<msg>|gmail:<id> ...
  todo-req cal_update --event-id E [--summary S] [--start ... --end ... --tz ... --weekday ...]
                      [--location L] [--description D | --description-append D] --evidence ... ...
  todo-req cal_delete --event-id E --summary S --evidence ... ...
                      (one of his own private events with no guests; S = the title he expects. The applier
                      checks the title and that the event's date is in his message or the evidence; undo recreates)
Prints "<id> <path>". Exit 1 = refused (reason on stderr), 2 = usage.

Settings: factory.env through lib/kitenv.py (real env wins): TODO_OUTBOX (default <WORK_DIR>/todo-outbox) and
PROTECTED_DIRS (folders the outbox may never be inside; the kit itself is always included).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import kitenv  # noqa: E402

FORMAT = "todo-request/1"
TODO_OPS = {
    "add_task": ({"title"}, {"due", "status"}),
    "add_event": ({"date", "title"}, {"start", "end", "tz", "note"}),
    "note": ({"anchor", "text"}, set()),
    "done": ({"anchor"}, set()),
    "undo": ({"target"}, set()),
}
CAL_OPS = {
    "cal_create": ({"summary", "start", "end", "tz", "weekday"}, {"all_day", "location", "description"}),
    "cal_update": ({"event_id"}, {"summary", "start", "end", "tz", "weekday", "location", "description",
                                  "description_append"}),
    "cal_delete": ({"event_id", "summary"}, set()),
}
OPS = dict(TODO_OPS, **CAL_OPS)
MAX_FIELD = 300
MAX_QUOTE = 500
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
CAL_URL_HOSTS = ("meet.google.com", "zoom.us", "teams.microsoft.com")
ID_RE = re.compile(r"^todo-\d{8}-\d{6}-[0-9a-f]{4}$")
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,99}$")


class Refused(Exception):
    pass


def unsafe(text):
    """Why text cannot go into his todo honestly, or None (control, format/bidi, separators, private use)."""
    for ch in text:
        if unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Zl", "Zp", "Cn") or ch == "ㅤ":
            return "contains a hidden or control character U+%04X (or a line break)" % ord(ch)
    return None


def check_text(name, value, table_cell=False, urls="none"):
    if not isinstance(value, str):
        raise Refused("%s must be text" % name)
    if not value.strip():
        raise Refused("%s is empty" % name)
    if len(value) > MAX_FIELD:
        raise Refused("%s is longer than %d characters" % (name, MAX_FIELD))
    if "\n" in value or "\r" in value:
        raise Refused("%s must be one line" % name)
    why = unsafe(value)
    if why:
        raise Refused("%s %s" % (name, why))
    if "<!--" in value or "-->" in value:
        raise Refused("%s may not contain <!-- or --> (markers are not allowed)" % name)
    if value.lstrip().startswith("#"):
        raise Refused("%s may not start with # (no headings)" % name)
    if table_cell and "|" in value:
        raise Refused("%s may not contain | (table cell)" % name)
    for url in URL_RE.findall(value):
        if urls == "none":
            raise Refused("%s contains a URL; URLs are not allowed in todo requests (v1)" % name)
        host = re.sub(r"(?i)^(https?://)?", "", url).split("/")[0].split(":")[0].lower()
        if not any(host == h or host.endswith("." + h) for h in CAL_URL_HOSTS):
            raise Refused("%s URL host %s is not allowed (only %s)" % (name, host, ", ".join(CAL_URL_HOSTS)))


def check_fields(op, fields):
    if op not in OPS:
        raise Refused("op %r is not one of %s (no todo delete, no free replace)" % (op, ", ".join(OPS)))
    if not isinstance(fields, dict):
        raise Refused("fields must be an object")
    need, may = OPS[op]
    if "attendees" in fields or "add_attendee" in fields:
        raise Refused("attendees are never allowed (self-only calendar)")
    extra = set(fields) - need - may
    if extra:
        raise Refused("unknown fields for %s: %s" % (op, sorted(extra)))
    missing = need - set(fields)
    if missing:
        raise Refused("missing fields for %s: %s" % (op, sorted(missing)))
    for k, v in fields.items():
        if k == "all_day":
            if not isinstance(v, bool):
                raise Refused("all_day must be true or false")
            continue
        check_text(k, v, table_cell=(op == "add_event"),
                   urls="calendar" if op in CAL_OPS and k in ("summary", "location", "description",
                                                               "description_append") else "none")
    if op == "undo" and not TARGET_RE.match(fields["target"]):
        raise Refused("target must be a request id")
    if op in ("note", "done"):
        a = fields["anchor"].strip()
        if not re.fullmatch(r"P-\d{4}", a) and len(a) < 4:
            raise Refused("anchor must be a P-xxxx ref or at least 4 characters of exact text")
    if "event_id" in fields and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_@.-]{2,199}", fields["event_id"]):
        raise Refused("event_id must be a Google Calendar event id")
    if op == "cal_update":
        changes = set(fields) - {"event_id", "tz", "weekday"}
        if not changes:
            raise Refused("cal_update needs at least one change")
        if "description" in fields and "description_append" in fields:
            raise Refused("give description or description_append, not both")
        if ("start" in fields) != ("end" in fields):
            raise Refused("give start and end together")
    if op in CAL_OPS and "start" in fields:
        if "tz" not in fields:
            raise Refused("a time needs an explicit IANA tz (no default: JST and PHT both happen)")
        if "weekday" not in fields:
            raise Refused("a date needs the weekday he expects (checked against the date)")


def check_evidence(evidence):
    if not isinstance(evidence, list) or not evidence or len(evidence) > 5:
        raise Refused("a calendar request needs evidence: 1-5 message or mail ids that prove it")
    for e in evidence:
        if not isinstance(e, dict) or e.get("kind") not in ("beeper", "gmail"):
            raise Refused("evidence items are {kind: beeper, chat_id, message_id} or {kind: gmail, message_id}")
        keys = {"kind", "chat_id", "message_id"} if e["kind"] == "beeper" else {"kind", "message_id"}
        if set(e) != keys or not all(isinstance(e[k], str) and e[k] and not unsafe(e[k]) for k in keys):
            raise Refused("evidence %s needs exactly %s" % (e.get("kind"), sorted(keys)))


def new_id(now):
    return "todo-%s-%s" % (now.strftime("%Y%m%d-%H%M%S"), secrets.token_hex(2))


def build_request(op, fields, message_id, quote, chat="self", evidence=None, now=None, rid=None):
    check_fields(op, fields)
    if not (isinstance(message_id, str) and message_id.strip() and len(message_id) <= 200 and not unsafe(message_id)):
        raise Refused("message_id (from the channel's message_id) is required")
    if not (isinstance(quote, str) and quote.strip() and len(quote) <= MAX_QUOTE and not unsafe(quote.replace("\n", " "))):
        raise Refused("quote must be his exact words, 1-%d characters" % MAX_QUOTE)
    if not (isinstance(chat, str) and chat and not unsafe(chat)):
        raise Refused("chat must be 'self' or his self-chat id")
    now = now or datetime.now(timezone.utc)
    req = {"format": FORMAT, "id": rid or new_id(now), "created_at": now.isoformat(), "op": op, "fields": fields,
           "source": {"message_id": message_id, "chat": chat}, "quote": quote}
    if op in CAL_OPS:
        check_evidence(evidence)
        req["evidence"] = evidence
    elif evidence:
        raise Refused("evidence is only for calendar requests")
    if not ID_RE.match(req["id"]):
        raise Refused("id must look like todo-YYYYMMDD-HHMMSS-xxxx")
    return req


# ---------------------------------------------------------------- where it may write

def protected_dirs():
    return [os.path.realpath(d) for d in kitenv.load()["PROTECTED_DIRS"]]


def default_outbox():
    work = kitenv.load()["WORK_DIR"]
    return Path(os.path.expanduser(os.environ.get("TODO_OUTBOX") or os.path.join(work, "todo-outbox")))


def _inside(path, folder):
    p, f = os.path.normcase(path), os.path.normcase(folder)
    return p == f or p.startswith(f.rstrip(os.sep) + os.sep)


def check_outbox(outbox):
    real = os.path.realpath(os.path.expanduser(str(outbox)))
    for d in protected_dirs():
        if _inside(real, d):
            raise Refused("outbox %s is inside protected folder %s; the factory never writes there" % (outbox, d))
    return Path(real)


def write_request(req, outbox=None):
    """Write <outbox>/<id>.json atomically; never replace an existing file. Returns the path."""
    outbox = check_outbox(outbox or default_outbox())
    build_request(req["op"], req["fields"], req["source"]["message_id"], req["quote"], req["source"]["chat"],
                  req.get("evidence"), datetime.fromisoformat(req["created_at"]), req["id"])
    outbox.mkdir(parents=True, exist_ok=True)
    final = outbox / (req["id"] + ".json")
    tmp = outbox / (".%s.%s.tmp" % (req["id"], secrets.token_hex(4)))
    data = (json.dumps(req, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(tmp, final)          # fails if the name exists: append-only, never overwrite
    except FileExistsError:
        raise Refused("%s already exists; a request is never overwritten" % final.name)
    finally:
        os.unlink(tmp)
    with open(outbox / "sent.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": req["id"], "op": req["op"], "created_at": req["created_at"],
                            "message_id": req["source"]["message_id"]}, ensure_ascii=False) + "\n")
    return final


# ---------------------------------------------------------------- CLI

FLAG_FIELDS = {"title", "due", "status", "date", "start", "end", "tz", "note", "anchor", "text", "target",
               "summary", "weekday", "location", "description", "description_append", "event_id"}


def parse_evidence(values):
    out = []
    for v in values or []:
        if v.startswith("gmail:"):
            out.append({"kind": "gmail", "message_id": v[len("gmail:"):]})
        elif v.startswith("beeper:"):
            chat, sep, msg = v[len("beeper:"):].rpartition(":")
            if not sep or not chat or not msg:
                raise Refused("--evidence beeper:<chat id>:<message id>")
            out.append({"kind": "beeper", "chat_id": chat, "message_id": msg})
        else:
            raise Refused("--evidence must start with beeper: or gmail:")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="todo-req", description=__doc__.split("\n")[0])
    ap.add_argument("op")
    for f in sorted(FLAG_FIELDS):
        ap.add_argument("--" + f.replace("_", "-"), dest=f)
    ap.add_argument("--all-day", dest="all_day", action="store_true")
    ap.add_argument("--message-id", required=True)
    ap.add_argument("--quote", required=True)
    ap.add_argument("--chat", default="self")
    ap.add_argument("--evidence", action="append")
    a = ap.parse_args(argv)
    fields = {f: getattr(a, f) for f in FLAG_FIELDS if getattr(a, f) is not None}
    if a.all_day:
        fields["all_day"] = True
    try:
        req = build_request(a.op, fields, a.message_id, a.quote, a.chat, parse_evidence(a.evidence) or None)
        path = write_request(req)
    except Refused as e:
        print("todo-req: refused: %s" % e, file=sys.stderr)
        return 1
    print("%s %s" % (req["id"], path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
