#!/usr/bin/env python3
"""Make the test fixtures from real transcripts (run once by hand; the output is committed).

Keeps only what run_budget reads: row type, timestamp, agentId, message id, model, usage, tool_use names/ids,
tool_result ids and is_error. Text, thinking, prompts and tool inputs are replaced by a length marker, so no
content from the original runs is kept ("file names and IDs only", SPEC Done-when 3). With --keep-inputs the
tool inputs and results stay (used for the probe transcripts, which only ran `echo`).
Home-directory paths are rewritten to /Users/you.

Usage: make_run_budget_fixtures.py [--keep-inputs] <in.jsonl> <out.jsonl>
"""
import json
import os
import re
import sys

HOME = os.path.expanduser("~")


def scrub(s):
    return s.replace(HOME, "/Users/you")


def redact(v):
    return "<redacted %d chars>" % len(json.dumps(v))


def block(b, keep):
    t = b.get("type")
    if t == "tool_use":
        return {"type": t, "id": b.get("id"), "name": b.get("name"),
                "input": json.loads(scrub(json.dumps(b.get("input")))) if keep else {"redacted": redact(b.get("input"))}}
    if t == "tool_result":
        c = b.get("content")
        return {"type": t, "tool_use_id": b.get("tool_use_id"), "is_error": bool(b.get("is_error")),
                "content": scrub(c if isinstance(c, str) else json.dumps(c)) if keep else redact(c)}
    return {"type": t, "text": redact(b.get("text") or b.get("thinking") or "")}


def row(r, keep):
    out = {k: r[k] for k in ("type", "timestamp", "agentId", "isSidechain", "uuid") if k in r}
    if r.get("type") == "attachment":
        out["attachment"] = {"type": (r.get("attachment") or {}).get("type")}
        return out
    m = r.get("message")
    if isinstance(m, dict) and r.get("type") in ("user", "assistant"):
        c = m.get("content")
        mm = {k: m[k] for k in ("id", "model", "role", "usage", "stop_reason") if k in m}
        mm["content"] = [block(b, keep) for b in c] if isinstance(c, list) else redact(c)
        out["message"] = mm
    return out


def main(argv):
    keep = "--keep-inputs" in argv
    args = [a for a in argv[1:] if a != "--keep-inputs"]
    src, dst = args
    with open(src) as f, open(dst, "w") as g:
        for line in f:
            if line.strip():
                g.write(json.dumps(row(json.loads(line), keep), separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main(sys.argv)
