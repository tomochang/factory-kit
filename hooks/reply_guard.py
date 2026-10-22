#!/usr/bin/env python3
"""WhatsApp reply guard (T22), one script for two Claude Code hooks in the foreman's workspace.

PreToolUse on mcp__whatsapp__reply: denies a reply, says why and how to rewrite it, when
  - it is longer than REPLY_MAX_CHARS characters or REPLY_MAX_LINES lines (700 / 10);
  - its first line does not say what the owner must do: "You: ...", "Nothing needed ..." or "Got it ..." (a receipt);
  - it holds a local file path (~/, /Users/, /home/, /private/, /tmp/, /var/folders/) anywhere but on a last line
    starting "File:" (the owner reads on the phone and cannot open it);
  - it says done/ready/merged/green/passed about a PR or CI while `gh pr checks N --repo R` does not show every
    check completed and successful (or gh fails or times out after REPLY_GH_TIMEOUT s, or the PR is not named);
  - it would be reply number REPLY_BURST_MAX+1 within REPLY_BURST_SECONDS with no new message from the owner.
Stop (only in the foreman session, FACTORY_SESSION=1): sends the turn back while a message from the owner that this
channel run received has no reply after it. At most 3 times per message, then it lets the turn end.

State is written by the channel server (foreman/channel/whatsapp-channel.mjs) under <ASK_ME_STATE>/channel/:
inbox/<message id>.json {id, text, received_at, answered_at, stale, stop_blocks, receipt} and replies.jsonl
({t, text} per sent reply). Settings come from factory.env (lib/kitenv.py); real environment wins.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# --- settings (kit: factory.env through lib/kitenv.py)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import kitenv  # noqa: E402

DEFAULTS = {"REPLY_MAX_CHARS": 700, "REPLY_MAX_LINES": 10, "REPLY_BURST_MAX": 2, "REPLY_BURST_SECONDS": 120,
            "REPLY_GH_TIMEOUT": 20}


def settings():
    cfg = kitenv.load()
    out = {"STATE": Path(cfg["ASK_ME_STATE"]) / "channel", "OWNER": cfg["OWNER_NAME"] or "the owner",
           "GH_OWNER": cfg.get("REPLY_GH_OWNER", "")}
    for k, d in DEFAULTS.items():
        try:
            out[k] = max(1, int(str(cfg.get(k) or d)))
        except ValueError:
            out[k] = d
    return out
# --- end of settings


TOOL = "mcp__whatsapp__reply"
STOP_LIMIT = 3
FIRST_LINE = re.compile(r"^\s*(you\s*:|nothing needed\b|got it\b)", re.I)
LOCAL_PATH = re.compile(r"(?<![\w.:/-])(~/|/Users/|/home/|/private/|/tmp/|/var/folders/)[^\s'\"`)]*")
PR_URL = re.compile(r"https?://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")
SLUG_REF = re.compile(r"\b([\w.-]+)/([\w.-]+)#(\d+)\b")
NAME_PR = re.compile(r"\b([\w.-]+)\s+(?:PR|pull request)\s*#?(\d+)\b", re.I)
PR_IN = re.compile(r"\b(?:PR|pull request)\s*#?(\d+)\s+(?:on|in|of|for)\s+(?:([\w.-]+)/)?([\w.-]+)", re.I)
BARE_PR = re.compile(r"\b(?:PR|pull request)\s*#?\d+|#\d+\b", re.I)
CONTEXT = re.compile(r"\b(CI|checks?|PRs?|pull requests?|GitHub Actions)\b|github\.com/[^\s]+/pull/", re.I)
CLAIM = re.compile(r"\b(done|ready|merged|green|pass(?:ed|es|ing)?|succeeded|successful|all good)\b|✅", re.I)
NEGATION = re.compile(r"(\b(not|no|isn't|aren't|wasn't|weren't|never|nor)\b|n't)\W+(\w+\W+)?$", re.I)
NO_CHECKS = re.compile(r"\bno (CI|checks)\b", re.I)
STOPWORDS = {"the", "a", "an", "his", "her", "their", "your", "my", "our", "this", "that", "open", "new", "draft",
             "merged", "kit", "its", "same", "one", "first", "second", "next", "last", "see", "and", "or", "in", "on"}
SHAPE = ('Line 1 must say what {owner} has to do: "You: <the one action>" or "Nothing needed - <status in one line>"'
         ' ("Got it - <what happens next>" for a receipt). Then at most {lines} lines and {chars} characters in total,'
         ' one message, the content itself instead of file paths.')


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def read_json(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def inbox(st):
    d = st["STATE"] / "inbox"
    if not d.is_dir():
        return []
    items = []
    for f in sorted(d.glob("*.json")):
        data = read_json(f)
        if isinstance(data, dict):
            items.append((f, data))
    return items


def replies(st):
    out = []
    try:
        lines = (st["STATE"] / "replies.jsonl").read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            out.append(float(json.loads(line)["t"]))
        except (ValueError, KeyError, TypeError):
            continue
    return out


# --- PreToolUse rules

def length_problems(text, st):
    out = []
    if len(text) > st["REPLY_MAX_CHARS"]:
        out.append(f"It is {len(text)} characters; the limit is {st['REPLY_MAX_CHARS']} characters. Cut it to the "
                   "one thing that matters now; drop history, apologies and repeated context.")
    n = len(text.splitlines())
    if n > st["REPLY_MAX_LINES"]:
        out.append(f"It has {n} lines; the limit is {st['REPLY_MAX_LINES']} lines. Merge or drop lines; do not "
                   "split it into several messages.")
    return out


def first_line_problem(text):
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    if FIRST_LINE.match(first):
        return None
    return (f'The first line ("{first[:60]}") does not say what he must do. Start with "You: <action>" when he has '
            'to do something, "Nothing needed - <status>" when he does not, or "Got it - <next step>" for a receipt.')


def path_problem(text):
    lines = text.splitlines()
    body = lines[:-1] if lines and lines[-1].lstrip().startswith("File:") else lines
    hits = [m.group(0) for ln in body for m in LOCAL_PATH.finditer(ln)]
    if not hits:
        return None
    return (f"It points at a local path ({hits[0]}) he cannot open on the phone. Put the content he needs into the "
            'message itself (in a few words). If the file must be named, put it alone on a last line "File: <path>".')


def pr_refs(text, gh_owner):
    """[(slug or None, number, as written)] for every PR the text names; slug None = repo unknown."""
    refs, spans = {}, []

    def add(slug, n, m):
        refs.setdefault((slug, int(n)), m.group(0))
        spans.append(m.span())

    for m in PR_URL.finditer(text):
        add(f"{m.group(1)}/{m.group(2)}", m.group(3), m)
    for m in SLUG_REF.finditer(text):
        if not any(a <= m.start() < b for a, b in spans):
            add(f"{m.group(1)}/{m.group(2)}", m.group(3), m)
    for m in PR_IN.finditer(text):
        if m.group(3).lower() in STOPWORDS:
            continue  # "PR #4 on the Mac": no repo named
        if not any(a <= m.start() < b for a, b in spans):
            owner = m.group(2) or gh_owner
            add(f"{owner}/{m.group(3)}" if owner else None, m.group(1), m)
    for m in NAME_PR.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        name = m.group(1)
        if name.lower() in STOPWORDS or name.isdigit():
            continue
        add(f"{gh_owner}/{name}" if gh_owner else None, m.group(2), m)
    for m in BARE_PR.finditer(text):
        if not any(a <= m.start() < b for a, b in spans):
            add(None, re.sub(r"\D", "", m.group(0)), m)
    return [(slug, n, w) for (slug, n), w in refs.items()]


def claims(text):
    out = []
    for m in CLAIM.finditer(text):
        before = text[max(0, m.start() - 24):m.start()]
        if NEGATION.search(before):
            continue
        out.append(m.group(0))
    return out


def gh_checks(slug, n, timeout):
    """(green, detail). green only when gh lists checks and every one passed (or was skipped)."""
    gh = shutil.which("gh")
    if not gh:
        return False, "gh is not installed here"
    try:
        r = subprocess.run([gh, "pr", "checks", str(n), "--repo", slug, "--json", "name,state,bucket"],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"could not check: gh timed out after {timeout} s"
    try:
        checks = json.loads(r.stdout) if r.stdout.strip() else None
    except ValueError:
        checks = None
    if checks is None:
        err = (r.stderr or "").strip().splitlines()
        if err and "no checks reported" in err[0]:
            return False, "no checks"
        return False, "could not check: " + (err[0][:120] if err else f"gh exited {r.returncode}")
    if not checks:
        return False, "no checks"
    bad = [f"{c.get('name')}={c.get('bucket') or c.get('state')}" for c in checks
           if c.get("bucket") not in ("pass", "skipping")]
    if bad:
        return False, "not all green: " + ", ".join(bad[:5])
    return True, f"{len(checks)} checks green"


def claim_problems(text, st):
    said = claims(text)
    if not said:
        return []
    refs = pr_refs(text, st["GH_OWNER"])
    if not refs and not CONTEXT.search(text):
        return []  # e.g. "40 tests passed locally": no PR or CI claim
    if not refs:
        return [f'It claims "{said[0]}" about CI or a PR without naming the PR. Put the full PR URL in the reply '
                "so the guard can run gh pr checks on it, or drop the claim."]
    out = []
    for slug, n, written in refs:
        if slug is None:
            out.append(f'It claims "{said[0]}" but "{written}" does not say which repo. Write the full PR URL '
                       "(https://github.com/<owner>/<repo>/pull/<n>) so the guard can run gh pr checks on it.")
            continue
        green, detail = gh_checks(slug, n, st["REPLY_GH_TIMEOUT"])
        if green:
            continue
        if detail == "no checks" and NO_CHECKS.search(text):
            continue
        if detail == "no checks":
            out.append(f'It claims "{said[0]}" but {slug}#{n} has no checks (gh pr checks found none). Say '
                       '"no CI checks on this PR" instead of green/passed.')
        elif detail.startswith("could not"):
            out.append(f'It claims "{said[0]}" for {slug}#{n}, but the guard {detail}. Say "checks not verified" '
                       "instead, or check again with gh pr checks and reply when it works.")
        else:
            out.append(f'It claims "{said[0]}" but {slug}#{n} is {detail} (gh pr checks). Wait with '
                       "`gh pr checks --watch`, fix failures, and report only when every check is green; or say "
                       'plainly "CI still running" / "CI failed: <check>".')
    return out


def burst_problem(st):
    now = time.time()
    last_his = max([float(d.get("received_at") or 0) for _, d in inbox(st)] or [0])
    since = now - st["REPLY_BURST_SECONDS"]
    recent = [t for t in replies(st) if t > since and t > last_his]
    if len(recent) < st["REPLY_BURST_MAX"]:
        return None
    return (f"This would be the {ordinal(len(recent) + 1)} reply within {st['REPLY_BURST_SECONDS']} s with no new "
            "message from him. Put everything into one message: wait until there is something he must act on or a "
            "final result, then send one reply.")


def check_reply(text, st):
    text = text.strip()
    problems = length_problems(text, st)
    for p in (first_line_problem(text), path_problem(text)):
        if p:
            problems.append(p)
    problems += claim_problems(text, st)
    b = burst_problem(st)
    if b:
        problems.append(b)
    return problems


def deny(problems, st):
    reason = ("BLOCKED: this WhatsApp reply was not sent. Fix every point and call the reply tool again:\n- "
              + "\n- ".join(problems) + "\n"
              + SHAPE.format(owner=st["OWNER"], lines=st["REPLY_MAX_LINES"], chars=st["REPLY_MAX_CHARS"]))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False))


# --- Stop rule

def unanswered(st):
    sent = replies(st)
    out = []
    for f, d in inbox(st):
        if d.get("stale") or d.get("answered_at"):
            continue
        got = float(d.get("received_at") or 0)
        if any(t >= got for t in sent):
            continue
        if int(d.get("stop_blocks") or 0) >= STOP_LIMIT:
            continue
        out.append((f, d))
    return out


def check_stop(st):
    if os.environ.get("FACTORY_SESSION") != "1":
        return None
    waiting = unanswered(st)
    if not waiting:
        return None
    lines = []
    for f, d in waiting:
        d["stop_blocks"] = int(d.get("stop_blocks") or 0) + 1
        try:
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(d))
            os.replace(tmp, f)
        except OSError:
            pass
        when = time.strftime("%H:%M", time.localtime(float(d.get("received_at") or 0)))
        lines.append(f'- {when} "{str(d.get("text", ""))[:80]}" (message {d.get("id")})')
    return {"decision": "block", "reason": (
        f"[WHATSAPP] {st['OWNER']} wrote and has no reply yet; they are on the phone and see nothing else:\n"
        + "\n".join(lines) + "\n"
        "Before ending the turn, answer with the whatsapp reply tool in one short message: \"Got it - <what happens "
        "next>\", \"You: <what they must do>\" or \"Nothing needed - <status>\".")}


def main():
    try:
        ev = json.load(sys.stdin)
    except ValueError:
        return
    st = settings()
    if ev.get("hook_event_name") == "Stop":
        out = check_stop(st)
        if out:
            print(json.dumps(out, ensure_ascii=False))
        return
    if ev.get("tool_name") != TOOL:
        return
    text = (ev.get("tool_input") or {}).get("text")
    if not isinstance(text, str):
        return
    problems = check_reply(text, st)
    if problems:
        deny(problems, st)


if __name__ == "__main__":
    main()
