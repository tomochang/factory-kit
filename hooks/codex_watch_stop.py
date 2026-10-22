#!/usr/bin/env python3
"""Codex Stop hook (T18 R4): record the finished turn for the watchdog, which then tells the owner
"Codex finished: <repo> - <first line>" on its next check.

Writes <ASK_ME_STATE>/codex-watch/stops/<session>.<turn>.json (mode 600) with the session id, turn id, cwd and
only the first line of the last assistant message (secrets redacted). It never sends, never blocks the turn and
prints nothing; any problem is ignored so Codex is never held up. Registered by install.sh in
~/.codex/hooks.json as a second Stop entry beside ask_me_stop.py (Codex runs both at once); Codex runs it only
after you trust it in /hooks.
"""
import json
import os
import re
import sys
import time
from pathlib import Path


SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}"
    r"|AIza[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9._-]+"
    r"|(?<=://)[^/\s:@]+:[^@\s]+(?=@)"                                   # user:password@ in URLs
    r"|(?i:(?<=pass)(?:word)?\s*[:=]\s*\S+|(?<=secret)\s*[:=]\s*\S+|(?<=token)\s*[:=]\s*\S+"
    r"|(?<=api_key)\s*[:=]\s*\S+|(?<=api-key)\s*[:=]\s*\S+|(?<=apikey)\s*[:=]\s*\S+)"
    r"|(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}"
    r"|(?<![A-Za-z0-9/+=_-])(?=[A-Za-z0-9/+=_-]*\d)(?=[A-Za-z0-9/+=_-]*[a-z])(?=[A-Za-z0-9/+=_-]*[A-Z])"
    r"[A-Za-z0-9/+=_-]{32,})")


def first_line(text, limit=100):
    """Same rule and SECRET_RE as watchdog.first_line (a test checks they match): first non-empty line, secrets redacted, at most `limit` characters."""
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            line = SECRET_RE.sub("[redacted]", line)
            return line if len(line) <= limit else line[:limit].rstrip() + "…"
    return ""


def safe(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:80] or "_"


def main():
    try:
        data = json.loads(sys.stdin.read() or "null")
    except ValueError:
        return
    if not isinstance(data, dict) or not data.get("session_id"):
        return
    sid, turn = str(data["session_id"]), str(data.get("turn_id") or "")
    rec = {"session_id": sid, "turn_id": turn, "cwd": str(data.get("cwd") or ""),
           "first_line": first_line(data.get("last_assistant_message")), "time": time.time()}
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    import kitenv  # inside main: a broken lib/ must not print or fail the hook
    d = Path(kitenv.load()["ASK_ME_STATE"]) / "codex-watch" / "stops"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    name = f"{safe(sid)}.{safe(turn)}.json"
    tmp = d / (name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, d / name)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
