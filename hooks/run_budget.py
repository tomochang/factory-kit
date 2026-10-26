#!/usr/bin/env python3
"""run-budget (T33): token/tool/minute caps, a stall stop and a cost log for every background run.

One script, three uses:

1. Claude Code hook (no arguments, event JSON on stdin), registered for PreToolUse, PostToolUse and
   PostToolUseFailure on every tool, and for SubagentStop and Stop (settings-hooks.json). It acts on a call only when
   the event carries an `agent_id`, so it never caps the foreman itself:
   - meter: per run, the hook's own tool-use count, minutes since the run started, and the run's transcript
     (`<session>/subagents/agent-<id>.jsonl`, or `.../workflows/<wf>/agent-<id>.jsonl`), read from a saved offset;
     usage de-duplicated by message.id; context tokens = the last request's total (the notification's number).
   - ladder (OpenRouter observe/block/stop): warn at a warn cap (the call goes ahead with a "wrap up" note);
     block at a hard cap, a stall or too many failing CI rounds (every tool is denied except the save set: git
     add/commit/status/diff/log in the run's own worktree, Write/Edit of PARTIAL.md, SubagentHandback); stop when the
     run makes RB_KILL_AFTER more calls after the block (deny + `continue: false`, row `killed:<reason>`).
   - stall rules (OpenHands StuckDetector, OpenClaw): the same tool with identical input and identical result is
     denied the 3rd time and blocks the run the 6th time; the same tool, input and error 3 times in a row blocks it.
     A changed result or a success resets; a bare `sleep` never counts; failing polling commands
     (`gh pr checks` while pending) do not count as errors.
   - cost log: one JSON row per run in <RUN_BUDGET_DIR>/runs.jsonl, written at SubagentStop (after polling the
     transcript for a late final row), by the hook that kills a run, or by the foreman's next Stop/PostToolUse for a
     run that ended without SubagentStop (maxTurns), using the completion notification.
   - foreman side: PostToolUse on Agent maps the new agentId to its description (T number); PreToolUse on the reply
     tool denies a final T report that has no `Cost:` line; PostToolUse on it marks the T as reported.
   It never deletes, resets or cleans anything. On its own failure it lets the call through and logs `hook-error`,
   except while the run is blocked, where a failure keeps the block.
2. `run-budget report T<n> [--label]`: the one-line Cost for a T report.
3. `run-budget audit --since YYYY-MM-DD [--projects DIR] [--replies FILE]`: the KGI and KPI checks (KGI.md).

Settings: real environment > the file named by RUN_BUDGET_CONF > run_budget.conf next to this script > the kit's
factory.env one folder up. Keys and defaults: DEFAULTS below. Per agent type: <KEY>__<AGENT_TYPE> (upper case,
"-" as "_"), e.g. RB_TOOLS_HARD__FACTORY_RESEARCHER=300. Per task: "budget: x2" in the Agent description (max
RB_MAX_MULT) scales the context, tool and minute caps.

Credits: the stall rules follow OpenHands' StuckDetector (MIT, learned from, no code copied; see REFERENCE.md),
the ladder OpenRouter's doom-loop detection, the error rule OpenClaw, the CI rounds Stripe's minions.
"""
import contextlib
import fcntl
import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

DEFAULTS = {
    "RUN_BUDGET_DIR": "~/factory/.runs",
    "RB_CTX_WARN": "300000",
    "RB_CTX_HARD": "400000",
    "RB_TOOLS_WARN": "150",
    "RB_TOOLS_HARD": "200",
    "RB_MIN_WARN": "45",
    "RB_MIN_HARD": "60",
    "RB_REPEAT_BLOCK": "3",
    "RB_REPEAT_STOP": "6",
    "RB_ERROR_STOP": "3",
    "RB_CI_ROUNDS": "2",
    "RB_KILL_AFTER": "10",
    "RB_MAX_MULT": "3",
    "RB_STOP_POLL_S": "5",
    "RB_POLL": r"\bgh\s+pr\s+checks\b|\bgh\s+run\s+(watch|view)\b|watchdog\S*\s+status\b|ask-me\.py\b.*--wait",
    "RB_REPLY_TOOL": "mcp__whatsapp__reply",
    "RB_REPLY_CHECK": "1",
    "RB_PROJECTS": "",
    "RB_REPLY_LOG": "",
}
CAPS = OrderedDict([
    ("ctx_warn", "RB_CTX_WARN"), ("ctx_hard", "RB_CTX_HARD"),
    ("tools_warn", "RB_TOOLS_WARN"), ("tools_hard", "RB_TOOLS_HARD"),
    ("min_warn", "RB_MIN_WARN"), ("min_hard", "RB_MIN_HARD"),
    ("repeat_block", "RB_REPEAT_BLOCK"), ("repeat_stop", "RB_REPEAT_STOP"), ("error_stop", "RB_ERROR_STOP"),
    ("ci_rounds", "RB_CI_ROUNDS"), ("kill_after", "RB_KILL_AFTER"),
])
SCALED = ("ctx_warn", "ctx_hard", "tools_warn", "tools_hard", "min_warn", "min_hard")
PATH_KEYS = ("RUN_BUDGET_DIR", "RB_PROJECTS", "RB_REPLY_LOG")
FIELDS = ("agent_id", "t", "description", "agent_type", "model", "start", "end", "minutes", "tool_uses",
          "context_tokens", "tokens", "stop_reason", "worktree", "last_commit")
TOKEN_KEYS = ("input", "output", "cache_creation", "cache_read")
SAVE_GIT = {"add", "commit", "status", "diff", "log", "rev-parse", "show"}
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
HANDBACK_TOOLS = ("SubagentHandback",)
T_RE = re.compile(r"\bT(\d{1,4})\b")
BUDGET_RE = re.compile(r"\bbudget\s*:\s*x\s*(\d+(?:\.\d+)?)", re.I)
PUSH_RE = re.compile(r"\bgit\b[^\n;&|]*\bpush\b|safe-push")
SAFE_PUSH_RE = re.compile(r"safe-push")
CHECKS_RE = re.compile(r"\bgh\s+pr\s+checks\b")
FAIL_RE = re.compile(r"\bfail(?:ed|ing|ure|s)?\b", re.I)
SLEEP_RE = re.compile(r"\s*sleep\s+\d+(?:\.\d+)?[smhd]?\s*")
NOTE_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
COST_PLAIN = re.compile(r"(?mi)^\s*Cost\s*:")
COST_LABEL = re.compile(r"(?mi)^\s*Cost\s+(T\d{1,4})\s*:")
WORKTREE_HINT = re.compile(r"(/private/tmp/[\w.@+-]+|/tmp/[\w.@+-]+)")


# ---------------------------------------------------------------- settings

def _parse_env_file(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not m:
            continue
        v = m.group(2)
        if v[:1] in "'\"":
            end = v.find(v[0], 1)
            v = v[1:end] if end > 0 else v[1:]
        else:
            v = re.sub(r"\s+#.*$", "", v)
        out[m.group(1)] = v
    return out


def _expand(v, home):
    if not v:
        return v
    v = v.replace("${HOME}", home).replace("$HOME", home)
    if v.startswith("~"):
        v = home + v[1:]
    return v


def load_config(environ=None):
    env = os.environ if environ is None else environ
    home = env.get("HOME") or os.path.expanduser("~")
    files = [env["RUN_BUDGET_CONF"]] if env.get("RUN_BUDGET_CONF") else \
        [str(HERE / "run_budget.conf"), env.get("FACTORY_ENV") or str(HERE.parent / "factory.env")]
    cfg = dict(DEFAULTS)
    for f in files:
        try:
            text = Path(f).read_text()
        except OSError:
            continue
        cfg.update({k: v for k, v in _parse_env_file(text).items() if k.startswith("RB_") or k == "RUN_BUDGET_DIR"
                    or k in ("WORK_DIR", "ASK_ME_STATE")})
        break
    cfg.update({k: v for k, v in env.items() if v != "" and (k.startswith("RB_") or k == "RUN_BUDGET_DIR")})
    for k in PATH_KEYS + ("WORK_DIR", "ASK_ME_STATE"):
        if cfg.get(k):
            cfg[k] = _expand(cfg[k], home)
    cfg["_HOME"] = home
    return cfg


def _num(cfg, key, agent_type):
    at = re.sub(r"[^A-Z0-9]", "_", (agent_type or "").upper())
    for k in ((key + "__" + at) if at else None, key):
        if k and cfg.get(k) not in (None, ""):
            try:
                return float(cfg[k])
            except ValueError:
                pass
    return float(DEFAULTS[key])


def multiplier(cfg, description):
    m = BUDGET_RE.search(description or "")
    if not m:
        return 1.0
    return max(1.0, min(float(m.group(1)), _num(cfg, "RB_MAX_MULT", "")))


def caps(cfg, agent_type, description):
    mult = multiplier(cfg, description)
    out = {}
    for name, key in CAPS.items():
        v = _num(cfg, key, agent_type)
        if name in SCALED:
            v *= mult
        out[name] = int(v) if float(v).is_integer() else v
    out["mult"] = mult
    return out


def runs_dir(cfg):
    return Path(cfg.get("RUN_BUDGET_DIR") or _expand(DEFAULTS["RUN_BUDGET_DIR"], cfg.get("_HOME", "")))


# ---------------------------------------------------------------- time

def parse_ts(s):
    if isinstance(s, (int, float)):
        return float(s)
    if not s:
        return None
    s = str(s).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        s += "T00:00:00Z"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------- transcript meter

def find_transcript(transcript_path, agent_id):
    """The subagent's own transcript, derived from the foreman's transcript_path (probe 2 in REFERENCE.md)."""
    if not transcript_path or not agent_id:
        return None
    base = transcript_path[:-6] if transcript_path.endswith(".jsonl") else transcript_path
    p = Path(base) / "subagents" / ("agent-%s.jsonl" % agent_id)
    if p.exists():
        return p
    hits = glob.glob(os.path.join(glob.escape(base), "subagents", "workflows", "*", "agent-%s.jsonl" % agent_id))
    return Path(hits[0]) if hits else None


def _first_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def meter_update(m, path):
    """Read what was appended to the transcript since the last call; tolerant of junk and half-written rows."""
    try:
        size = os.path.getsize(path)
    except (OSError, TypeError):
        return m
    if m.get("path") != str(path) or size < m.get("offset", 0):
        m.clear()
        m["path"] = str(path)
    m.setdefault("offset", 0)
    m.setdefault("usage", {})
    m.setdefault("tools", [])
    if size == m["offset"]:
        return m
    with open(path, "rb") as f:
        f.seek(m["offset"])
        data = f.read(size - m["offset"])
    end = data.rfind(b"\n")
    if end < 0:
        return m
    m["offset"] += end + 1
    tools = set(m["tools"])
    for line in data[:end + 1].split(b"\n"):
        if not line.strip():
            continue
        want_first = "first_ts" not in m or "prompt" not in m
        if not want_first and b'"assistant"' not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        if "first_ts" not in m and r.get("timestamp"):
            m["first_ts"] = parse_ts(r["timestamp"])
        msg = r.get("message") if isinstance(r.get("message"), dict) else {}
        if "prompt" not in m and r.get("type") == "user":
            m["prompt"] = _first_text(msg.get("content"))[:4000]
        if r.get("type") != "assistant":
            continue
        u = msg.get("usage")
        mid = msg.get("id")
        if isinstance(u, dict) and mid:
            try:
                m["usage"][mid] = [int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                                   int(u.get("cache_creation_input_tokens") or 0),
                                   int(u.get("cache_read_input_tokens") or 0)]
            except (TypeError, ValueError):
                continue
            m["last_id"] = mid
            if msg.get("model"):
                m["model"] = msg["model"]
            if r.get("timestamp"):
                m["last_ts"] = parse_ts(r["timestamp"])
        for b in msg.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") and b["id"] not in tools:
                tools.add(b["id"])
                m["tools"].append(b["id"])
    return m


def meter_summary(m):
    usage = (m or {}).get("usage") or {}
    if not usage:
        return {"context_tokens": "unknown", "cumulative": dict({k: "unknown" for k in TOKEN_KEYS}, total="unknown"),
                "tool_uses": "unknown", "requests": 0}
    sums = [sum(v[i] for v in usage.values()) for i in range(4)]
    cum = dict(zip(TOKEN_KEYS, sums))
    cum["total"] = sum(sums)
    return {"context_tokens": sum(usage[m["last_id"]]), "cumulative": cum, "tool_uses": len(m.get("tools") or []),
            "requests": len(usage)}


def transcript_totals(path):
    m = meter_update({}, path)
    out = meter_summary(m)
    out.update(first_ts=m.get("first_ts"), last_ts=m.get("last_ts"), model=m.get("model"), prompt=m.get("prompt"))
    return out


# ---------------------------------------------------------------- notifications

def _strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)


def _tag(text, name):
    m = re.search(r"<%s>(.*?)</%s>" % (name, name), text, re.S)
    return m.group(1).strip() if m else None


def notifications_from_lines(lines, out=None):
    out = {} if out is None else out
    for line in lines:
        if "<task-notification>" not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        for s in _strings(r):
            for body in NOTE_RE.findall(s):
                tid = _tag(body, "task-id")
                if not tid:
                    continue
                n = {"status": _tag(body, "status"), "summary": _tag(body, "summary") or ""}
                for k in ("subagent_tokens", "tool_uses", "duration_ms"):
                    v = _tag(body, k)
                    if v and v.isdigit():
                        n[k] = int(v)
                out[tid] = n
    return out


def parse_notifications(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return notifications_from_lines(f)
    except OSError:
        return {}


# ---------------------------------------------------------------- state

def _state_path(cfg, aid):
    return runs_dir(cfg) / "state" / ("%s.json" % re.sub(r"[^\w.-]", "_", aid))


def _read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def _write_json(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    os.replace(tmp, p)


@contextlib.contextmanager
def locked(cfg, aid):
    p = _state_path(cfg, aid).with_suffix(".lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_state(cfg, aid):
    return _read_json(_state_path(cfg, aid))


def save_state(cfg, st):
    _write_json(_state_path(cfg, st["agent_id"]), st)


def new_state(ev, now):
    return {"agent_id": ev["agent_id"], "agent_type": ev.get("agent_type") or "", "session_id": ev.get("session_id"),
            "main_transcript": ev.get("transcript_path"), "first_seen": now, "last_seen": now, "calls": 0,
            "stage": "ok", "reason": None, "after_block": 0, "warned": [], "recent": [], "rep_key": None,
            "rep_denied": 0, "err_key": None, "err_streak": 0, "pushed": False, "ci_rounds": 0, "worktree": None,
            "description": None, "meter": {}, "hook_errors": 0, "rows": 0, "row_at": 0}


def _meta_path(st):
    tp = st.get("transcript")
    if tp:
        return tp[:-6] + ".meta.json"
    main = st.get("main_transcript") or ""
    base = main[:-6] if main.endswith(".jsonl") else main
    return os.path.join(base, "subagents", "agent-%s.meta.json" % st["agent_id"]) if base else ""


def _refresh(cfg, st, ev=None):
    if ev:
        if ev.get("agent_type"):
            st["agent_type"] = ev["agent_type"]
        if ev.get("transcript_path"):
            st["main_transcript"] = ev["transcript_path"]
        if ev.get("agent_transcript_path") and os.path.exists(ev["agent_transcript_path"]):
            st["transcript"] = ev["agent_transcript_path"]
    if not st.get("transcript") or not os.path.exists(st["transcript"]):
        p = find_transcript(st.get("main_transcript"), st["agent_id"])
        if p:
            st["transcript"] = str(p)
    if st.get("transcript"):
        meter_update(st["meter"], st["transcript"])
    if not st.get("description"):
        mapped = (_read_json(runs_dir(cfg) / "agents.json", {}) or {}).get(st["agent_id"]) or {}
        meta = _read_json(_meta_path(st), {}) if _meta_path(st) else {}
        st["description"] = mapped.get("description") or (meta or {}).get("description") or None
        if meta and meta.get("agentType") and not st.get("agent_type"):
            st["agent_type"] = meta["agentType"]


def _start(st):
    ft = (st.get("meter") or {}).get("first_ts")
    return min(ft, st["first_seen"]) if ft else st["first_seen"]


def _minutes(st, now):
    return max(0.0, (now - _start(st)) / 60.0)


# ---------------------------------------------------------------- hashing and command shapes

def _h(v):
    return hashlib.sha1(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()[:16]


def shell_tokens(cmd):
    """Shell words and operators; quotes resolved, comments dropped, an unquoted newline kept as an operator."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = "#"
    return list(lex)


def canonical_command(cmd):
    """A Bash command without shell comments and extra spaces: `echo x # call 3 of 10` is `echo x` (live check)."""
    try:
        toks = shell_tokens(cmd)
    except ValueError:
        return " ".join(cmd.split())
    while toks and toks[-1] == "\n":
        toks.pop()
    return " ".join(t if t in ("\n", "&&", ";", "|", "||") else shlex.quote(t) for t in toks)


def input_hash(tool, inp):
    if isinstance(inp, dict):
        inp = {k: v for k, v in inp.items() if k != "description"}
        if tool == "Bash" and isinstance(inp.get("command"), str):
            inp["command"] = canonical_command(inp["command"])
    return _h([tool, inp])


def _clean_response(resp):
    if isinstance(resp, dict):
        return {k: v for k, v in resp.items() if not re.search(r"duration|time|elapsed", k, re.I)}
    return resp


def _text(v):
    """The readable text of a tool result (stdout, stderr, error), for the CI-failure check."""
    if isinstance(v, str):
        return v
    return "\n".join(_strings(v)) if isinstance(v, (dict, list)) else str(v)


def _cmd(tool, inp):
    return str((inp or {}).get("command") or "") if tool == "Bash" and isinstance(inp, dict) else ""


def is_poll(cfg, tool, inp):
    c = _cmd(tool, inp)
    return bool(c) and bool(re.search(cfg.get("RB_POLL") or DEFAULTS["RB_POLL"], c))


def _under(path, root):
    try:
        path, root = os.path.realpath(path), os.path.realpath(root)
    except (TypeError, ValueError):
        return False
    return path == root or path.startswith(root.rstrip("/") + "/")


def _toplevel(d):
    try:
        r = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    return os.path.realpath(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def _dirs_in(cmd, cwd):
    out = []
    for m in re.finditer(r"(?:\bgit\s+-C|\bcd)\s+('[^']+'|\"[^\"]+\"|\S+)", cmd):
        d = os.path.expanduser(m.group(1).strip("'\""))
        out.append(d if os.path.isabs(d) else os.path.join(cwd or "/", d))
    return out


def safe_git(cmd, wt):
    """True when every part of the command is `cd <dir>` or a save-set git command in the run's worktree."""
    if "`" in cmd or "$(" in cmd:
        return False
    try:
        toks = shell_tokens(cmd)
    except ValueError:
        return False
    parts, cur_part = [], []
    for t in toks:
        if t in ("&&", ";", "\n"):
            if cur_part:
                parts.append(cur_part)
            cur_part = []
        elif t and set(t) <= set(";&|<>()"):
            return False  # a pipe, redirect, background or subshell
        else:
            cur_part.append(t)
    if cur_part:
        parts.append(cur_part)
    if not parts:
        return False
    cur = None
    for toks in parts:
        if toks[0] == "cd" and len(toks) == 2:
            cur = os.path.expanduser(toks[1])
            if wt and not _under(cur, wt):
                return False
            continue
        if toks[0] != "git":
            return False
        i, d = 1, cur
        while i < len(toks) and toks[i].startswith("-"):
            if toks[i] == "-C" and i + 1 < len(toks):
                d = os.path.expanduser(toks[i + 1])
                i += 2
                continue
            return False
        if i >= len(toks) or toks[i] not in SAVE_GIT:
            return False
        if wt and (d is None or not _under(d, wt)):
            return False
    return True


def in_save_set(tool, inp, st):
    wt = st.get("worktree")
    inp = inp if isinstance(inp, dict) else {}
    if tool in HANDBACK_TOOLS:
        return True
    if tool in EDIT_TOOLS:
        fp = str(inp.get("file_path") or inp.get("notebook_path") or "")
        return os.path.basename(fp) == "PARTIAL.md" and (not wt or _under(fp, wt))
    if tool == "Bash":
        return safe_git(_cmd(tool, inp), wt)
    return False


# ---------------------------------------------------------------- hook outputs

def _deny(text):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": text}}


def _note(text):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


def block_message(st, c):
    wt = st.get("worktree") or "<your worktree>"
    left = max(0, c["kill_after"] - st.get("after_block", 0))
    return ("run-budget: %s. Stop working on the task now. Commit what you have in your worktree "
            "(`git -C %s add -A`, then `git -C %s commit -m \"partial: ...\"`, as separate calls), write PARTIAL.md "
            "there (what is finished, what is left, how to resume), then hand back with a report starting "
            "`STOPPED: %s`. Every other tool is denied; after %d more calls this run is ended."
            % (st["reason"], wt, wt, st["reason"], left))


def kill_output(st):
    text = ("run-budget: killed:%s. This run made %s calls after it was blocked and is now stopped. Its worktree "
            "and commits are kept; the foreman can resume it with SendMessage." % (st["reason"], st.get("after_block")))
    out = _deny(text)
    out["continue"] = False
    out["stopReason"] = text
    return out


# ---------------------------------------------------------------- cost rows

def _last_commit(wt):
    if not wt or not os.path.isdir(wt):
        return "none"
    try:
        r = subprocess.run(["git", "-C", wt, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "none"


def _t_number(st):
    for text in (st.get("description"), (st.get("meter") or {}).get("prompt")):
        m = T_RE.search(text or "")
        if m:
            return "T" + m.group(1)
    return "none"


def _guess_worktree(st):
    if st.get("worktree"):
        return st["worktree"]
    for m in WORKTREE_HINT.finditer((st.get("meter") or {}).get("prompt") or ""):
        d = m.group(1).rstrip(".,)")
        if os.path.isdir(d):
            top = _toplevel(d)
            if top and _under(top, d):
                return top
    return None


def make_row(cfg, st, now, stop_reason, end=None, note=None):
    s = meter_summary(st.get("meter"))
    c = caps(cfg, st.get("agent_type"), st.get("description"))
    start = _start(st)
    end = end or now
    wt = _guess_worktree(st)
    row = OrderedDict([
        ("agent_id", st["agent_id"]), ("t", _t_number(st)), ("description", st.get("description") or "none"),
        ("agent_type", st.get("agent_type") or "unknown"), ("model", (st.get("meter") or {}).get("model") or "unknown"),
        ("start", iso(start)), ("end", iso(end)), ("minutes", round(max(0.0, end - start) / 60.0, 1)),
        ("tool_uses", s["tool_uses"] if isinstance(s["tool_uses"], int) and s["tool_uses"] else st.get("calls", 0)),
        ("context_tokens", s["context_tokens"]), ("tokens", s["cumulative"]), ("requests", s["requests"]),
        ("stop_reason", stop_reason), ("worktree", wt or "none"), ("last_commit", _last_commit(wt)),
        ("session_id", st.get("session_id")), ("hook_calls", st.get("calls", 0)),
        ("hook_errors", st.get("hook_errors", 0)), ("caps", c),
    ])
    if note:
        row["notification"] = {k: note[k] for k in ("subagent_tokens", "tool_uses") if k in note}
    return row


def write_row(cfg, row):
    p = runs_dir(cfg) / "runs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def read_rows(cfg, all_rows=False):
    rows = []
    try:
        lines = (runs_dir(cfg) / "runs.jsonl").read_text().splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("agent_id"):
            rows.append(r)
    if all_rows:
        return rows
    last = OrderedDict()
    for r in rows:
        last.pop(r["agent_id"], None)
        last[r["agent_id"]] = r
    return list(last.values())


def _finish(cfg, st, now, reason, end=None, note=None):
    write_row(cfg, make_row(cfg, st, now, reason, end=end, note=note))
    st["rows"] = st.get("rows", 0) + 1
    st["row_at"] = now


# ---------------------------------------------------------------- the ladder (subagent events)

def _enter_block(st, reason, now):
    if st["stage"] in ("block", "stop"):
        return
    st["stage"], st["reason"], st["after_block"], st["blocked_at"] = "block", reason, 0, now


def _repeat(st, tool, ih, c):
    """None, "soft" (deny this call, ask for a different approach) or "stall:repeat"."""
    key = [tool, ih]
    recent = st.get("recent") or []
    streak = 0
    if recent and recent[-1][:2] == key and not recent[-1][3]:
        rh0 = recent[-1][2]
        for e in reversed(recent):
            if e[:2] == key and e[2] == rh0 and not e[3]:
                streak += 1
            else:
                break
    if streak == 0:
        st["rep_key"], st["rep_denied"] = None, 0
        return None
    denied = st.get("rep_denied", 0) if st.get("rep_key") == key else 0
    n = streak + denied + 1
    if n >= c["repeat_stop"]:
        return "stall:repeat"
    if n >= c["repeat_block"]:
        st["rep_key"], st["rep_denied"] = key, denied + 1
        return "soft"
    return None


def on_pre(ev, cfg, now):
    aid = ev["agent_id"]
    with locked(cfg, aid):
        st = load_state(cfg, aid) or new_state(ev, now)
        st["calls"] += 1
        st["last_seen"] = now
        _refresh(cfg, st, ev)
        c = caps(cfg, st.get("agent_type"), st.get("description"))
        tool, inp = ev.get("tool_name") or "", ev.get("tool_input") or {}
        try:
            if st["stage"] == "stop":
                return kill_output(st)
            if st["stage"] == "block":
                st["after_block"] += 1
                if st["after_block"] >= c["kill_after"]:
                    st["stage"] = "stop"
                    _finish(cfg, st, now, "killed:" + st["reason"])
                    return kill_output(st)
                return None if in_save_set(tool, inp, st) else _deny(block_message(st, c))
            ctx = meter_summary(st["meter"])["context_tokens"]
            mins = _minutes(st, now)
            reason = None
            if isinstance(ctx, int) and ctx >= c["ctx_hard"]:
                reason = "cap:context"
            elif st["calls"] > c["tools_hard"]:
                reason = "cap:tools"
            elif mins >= c["min_hard"]:
                reason = "cap:minutes"
            soft = None
            if not reason and not (tool == "Bash" and SLEEP_RE.fullmatch(_cmd(tool, inp))):
                r = _repeat(st, tool, input_hash(tool, inp), c)
                if r == "soft":
                    soft = r
                elif r:
                    reason = r
            if reason:
                _enter_block(st, reason, now)
                return None if in_save_set(tool, inp, st) else _deny(block_message(st, c))
            if soft:
                st["soft_denied"] = True
                n = c["repeat_block"] + st["rep_denied"] - 1
                return _deny("run-budget: you made the same %s call with the same input %d times and got the same "
                             "result each time. Repeating it will not help: take a different approach (change the "
                             "input, read the output again, or hand back with what you have). At %d identical "
                             "calls the run is stopped." % (tool, n, c["repeat_stop"]))
            notes = []
            if isinstance(ctx, int) and ctx >= c["ctx_warn"] and "context" not in st["warned"]:
                st["warned"].append("context")
                notes.append("context tokens %dk of %dk" % (ctx // 1000, c["ctx_hard"] // 1000))
            if st["calls"] >= c["tools_warn"] and "tools" not in st["warned"]:
                st["warned"].append("tools")
                notes.append("tool uses %d of %d" % (st["calls"], c["tools_hard"]))
            if mins >= c["min_warn"] and "minutes" not in st["warned"]:
                st["warned"].append("minutes")
                notes.append("minutes %d of %d" % (mins, c["min_hard"]))
            if notes:
                if st["stage"] == "ok":
                    st["stage"] = "warn"
                return _note("run-budget: this run is near its budget (%s). Time to wrap up: finish the current "
                             "step, commit your work in your worktree, and hand back with what is done and what is "
                             "left. At the hard cap every tool except git commit, PARTIAL.md and the hand-back is "
                             "denied." % "; ".join(notes))
            return None
        finally:
            save_state(cfg, st)


def on_post(ev, cfg, now):
    aid = ev["agent_id"]
    with locked(cfg, aid):
        st = load_state(cfg, aid) or new_state(ev, now)
        st["last_seen"] = now
        if ev.get("agent_type"):
            st["agent_type"] = ev["agent_type"]
        c = caps(cfg, st.get("agent_type"), st.get("description"))
        tool, inp = ev.get("tool_name") or "", ev.get("tool_input") or {}
        failed = ev.get("hook_event_name") == "PostToolUseFailure"
        result = ev.get("error") if failed else _clean_response(ev.get("tool_response"))
        ih, rh = input_hash(tool, inp), _h(result)
        if not in_save_set(tool, inp, st):
            st["soft_denied"] = False  # it went on with other work after a repeat deny: changed course
        st["recent"] = (st.get("recent") or [])[-11:] + [[tool, ih, rh, failed]]
        poll = is_poll(cfg, tool, inp)
        if failed and not poll:
            ekey = [tool, ih, rh]
            st["err_streak"] = st.get("err_streak", 0) + 1 if st.get("err_key") == ekey else 1
            st["err_key"] = ekey
            if st["err_streak"] >= c["error_stop"]:
                _enter_block(st, "stall:error", now)
        elif not failed:
            st["err_streak"], st["err_key"] = 0, None
        cmd = _cmd(tool, inp)
        text = _text(result)
        if cmd:
            if PUSH_RE.search(cmd):
                if failed and SAFE_PUSH_RE.search(cmd):
                    st["ci_rounds"] = st.get("ci_rounds", 0) + 1
                elif not failed:
                    st["pushed"] = True
            if CHECKS_RE.search(cmd) and FAIL_RE.search(text) and st.get("pushed"):
                st["ci_rounds"] = st.get("ci_rounds", 0) + 1
                st["pushed"] = False
            if st.get("ci_rounds", 0) >= c["ci_rounds"]:
                _enter_block(st, "ci-rounds", now)
            if not failed and re.search(r"\bgit\b", cmd) and "commit" in cmd:
                for d in _dirs_in(cmd, ev.get("cwd")):
                    top = _toplevel(d) if os.path.isdir(d) else None
                    if top:
                        st["worktree"] = top
        if tool in EDIT_TOOLS and not failed and not st.get("worktree") and isinstance(inp, dict):
            d = os.path.dirname(str(inp.get("file_path") or ""))
            top = _toplevel(d) if d and os.path.isdir(d) else None
            if top:
                st["worktree"] = top
        save_state(cfg, st)
    return None


def on_subagent_stop(ev, cfg, now):
    aid = ev["agent_id"]
    with locked(cfg, aid):
        st = load_state(cfg, aid)
        if not st and not ev.get("agent_type"):
            return None  # a helper subagent of the session (probe 7), not a background run
        st = st or new_state(ev, now)
        _refresh(cfg, st, ev)
        poll = float(cfg.get("RB_STOP_POLL_S") or 0)
        tp = st.get("transcript")
        if poll > 0 and tp:
            deadline = time.time() + poll
            last = os.path.getsize(tp) if os.path.exists(tp) else -1
            stable = time.time()
            while time.time() < deadline:
                time.sleep(0.2)
                size = os.path.getsize(tp) if os.path.exists(tp) else -1
                if size != last:
                    last, stable = size, time.time()
                    meter_update(st["meter"], tp)
                elif time.time() - stable >= 1.0:
                    break
        if st["stage"] != "stop":
            reason = "completed" if st["stage"] in ("ok", "warn") else st["reason"]
            if reason == "completed" and st.get("soft_denied"):
                reason = "stall:repeat"  # it handed back while its repeated call was being denied (live check)
            _finish(cfg, st, now, reason)
            if st["stage"] == "block" and str(st["reason"]).startswith(("stall:", "ci-rounds")):
                # a resumed run starts its stall counts fresh (K4 counts the resumes that then finish)
                st.update(stage="ok", reason=None, after_block=0, recent=[], rep_key=None, rep_denied=0,
                          err_key=None, err_streak=0, ci_rounds=0, pushed=False)
        st["last_seen"] = now
        st["row_at"] = now
        save_state(cfg, st)
    return None


# ---------------------------------------------------------------- foreman events

def _sweep(ev, cfg, now):
    """Rows for runs that ended without SubagentStop (maxTurns, probe 5), from the completion notifications."""
    d = runs_dir(cfg)
    sw = _read_json(d / "sweep.json", {}) or {}
    if now - float(sw.get("t") or 0) < 10:
        return
    sw["t"] = now
    notes = _read_json(d / "notifications.json", {}) or {}
    tp = ev.get("transcript_path")
    offs = sw.setdefault("offsets", {})
    if tp and os.path.exists(tp):
        size = os.path.getsize(tp)
        off = int(offs.get(tp) or 0)
        if size < off:
            off = 0
        if size > off:
            with open(tp, "rb") as f:
                f.seek(off)
                data = f.read(size - off)
            end = data.rfind(b"\n")
            if end >= 0:
                notifications_from_lines(data[:end + 1].decode("utf-8", "replace").splitlines(), notes)
                offs[tp] = off + end + 1
        _write_json(d / "notifications.json", notes)
    for f in glob.glob(str(d / "state" / "*.json")):
        st = _read_json(f)
        if not isinstance(st, dict) or st.get("stage") == "stop":
            continue
        if float(st.get("row_at") or 0) >= float(st.get("last_seen") or 0) or now - float(st["last_seen"]) < 5:
            continue
        meta = _read_json(_meta_path(st), {}) if _meta_path(st) else {}
        note = notes.get(st["agent_id"])
        if not ((meta or {}).get("finished") is True or note):
            continue
        with locked(cfg, st["agent_id"]):
            st = load_state(cfg, st["agent_id"])
            _refresh(cfg, st)
            if note and "turn limit" in (note.get("summary") or ""):
                reason = "killed:max-turns"
            elif st["stage"] == "block":
                reason = st["reason"]
            else:
                reason = "unknown"
            end = (st.get("meter") or {}).get("last_ts") or st["last_seen"]
            _finish(cfg, st, now, reason, end=max(end, _start(st)), note=note)
            save_state(cfg, st)
    _write_json(d / "sweep.json", sw)


def on_foreman(ev, cfg, now):
    name, tool = ev.get("hook_event_name"), ev.get("tool_name") or ""
    reply_tool = cfg.get("RB_REPLY_TOOL") or DEFAULTS["RB_REPLY_TOOL"]
    inp = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
    if name == "PreToolUse":
        if tool == reply_tool and cfg.get("RB_REPLY_CHECK", "1") == "1" and isinstance(inp.get("text"), str):
            problems = reply_problems(inp["text"], cfg)
            if problems:
                return _deny("BLOCKED: this reply was not sent.\n- " + "\n- ".join(problems))
        return None
    if name == "PostToolUse":
        if tool in ("Agent", "Task"):
            resp = ev.get("tool_response") if isinstance(ev.get("tool_response"), dict) else {}
            aid = resp.get("agentId") or resp.get("agent_id")
            if aid:
                p = runs_dir(cfg) / "agents.json"
                m = _read_json(p, {}) or {}
                m[aid] = {"description": inp.get("description") or resp.get("description"),
                          "subagent_type": inp.get("subagent_type"), "t": now}
                _write_json(p, m)
        if tool == reply_tool and isinstance(inp.get("text"), str):
            mark_reported(inp["text"], cfg, now)
    if name in ("PostToolUse", "Stop"):
        _sweep(ev, cfg, now)
    return None


def handle(ev, cfg, now=None):
    now = time.time() if now is None else now
    if not isinstance(ev, dict):
        return None
    name = ev.get("hook_event_name")
    if ev.get("agent_id"):
        if name == "PreToolUse":
            return on_pre(ev, cfg, now)
        if name in ("PostToolUse", "PostToolUseFailure"):
            return on_post(ev, cfg, now)
        if name == "SubagentStop":
            return on_subagent_stop(ev, cfg, now)
        return None
    return on_foreman(ev, cfg, now)


def run_hook(raw, cfg):
    """Hook entry: fail-open on any error, except that a run in the block stage stays blocked."""
    try:
        ev = json.loads(raw)
    except ValueError:
        return ""
    try:
        out = handle(ev, cfg)
        return json.dumps(out, ensure_ascii=False) if out else ""
    except Exception as e:  # noqa: BLE001 - the hook must never break a run by crashing
        aid = ev.get("agent_id") if isinstance(ev, dict) else None
        try:
            d = runs_dir(cfg)
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "hook-errors.log", "a") as f:
                f.write("%s hook-error %s %s %s: %s\n" % (iso(time.time()), ev.get("hook_event_name"), aid,
                                                         type(e).__name__, str(e)[:200]))
        except Exception:  # noqa: BLE001
            pass
        st = None
        if aid:
            try:
                st = load_state(cfg, aid)
                if st:
                    st["hook_errors"] = st.get("hook_errors", 0) + 1
                    save_state(cfg, st)
            except Exception:  # noqa: BLE001
                pass
        if st and st.get("stage") in ("block", "stop") and ev.get("hook_event_name") == "PreToolUse":
            if in_save_set_safe(ev, st):
                return ""
            return json.dumps(_deny("run-budget: this run is blocked (%s) and the budget hook hit an error, so the "
                                    "block stays. Commit your work, write PARTIAL.md and hand back starting "
                                    "`STOPPED: %s`." % (st.get("reason"), st.get("reason"))))
        return ""


def in_save_set_safe(ev, st):
    try:
        return in_save_set(ev.get("tool_name") or "", ev.get("tool_input") or {}, st)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- report and reply rule

def norm_t(t):
    t = str(t).strip()
    return t if t.upper().startswith("T") and t[1:].isdigit() else ("T" + t if t.isdigit() else t)


def report_line(cfg, t, label=False):
    t = norm_t(t)
    prefix = "Cost %s:" % t if label else "Cost:"
    rows = [r for r in read_rows(cfg) if r.get("t") == t]
    if not rows:
        return "%s no runs logged for %s" % (prefix, t)
    ctx = [r["context_tokens"] for r in rows if isinstance(r.get("context_tokens"), int)]
    totals = [(r.get("tokens") or {}).get("total") for r in rows]
    known = [x for x in totals if isinstance(x, int)]
    unknown = len(totals) - len(known)
    tools = sum(r["tool_uses"] for r in rows if isinstance(r.get("tool_uses"), int))
    mins = sum(float(r["minutes"]) for r in rows if isinstance(r.get("minutes"), (int, float)))
    stops = Counter(r.get("stop_reason") or "unknown" for r in rows)
    stop_text = next(iter(stops)) if len(stops) == 1 else ", ".join("%s %d" % (k, stops[k]) for k in sorted(stops))
    total = "total %dk%s tok%s" % (sum(known) // 1000, "+" if unknown else "",
                                   " (%d unknown)" % unknown if unknown else "")
    return "%s %d run%s, ctx max %s, %s, %d tools, %d min, stops: %s" % (
        prefix, len(rows), "" if len(rows) == 1 else "s", "%dk" % (max(ctx) // 1000) if ctx else "unknown", total,
        tools, int(round(mins)), stop_text)


def _reported_path(cfg):
    return runs_dir(cfg) / "reported.json"


def _ts_in(text):
    seen = []
    for m in T_RE.finditer(text):
        t = "T" + m.group(1)
        if t not in seen:
            seen.append(t)
    return seen


def pending_ts(text, cfg):
    reported = _read_json(_reported_path(cfg), {}) or {}
    rows = read_rows(cfg)
    out = []
    for t in _ts_in(text):
        ends = [parse_ts(r.get("end")) or 0 for r in rows if r.get("t") == t]
        if ends and max(ends) > float(reported.get(t) or 0):
            out.append(t)
    return out


def reply_problems(text, cfg):
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    if re.match(r"\s*got it\b", first, re.I):
        return []
    pending = pending_ts(text, cfg)
    if not pending:
        return []
    labelled = set(COST_LABEL.findall(text))
    missing = [t for t in pending if t not in labelled]
    if len(pending) == 1 and COST_PLAIN.search(text):
        missing = []
    many = len(pending) > 1
    return ['It reports %s, which has finished background runs not reported yet, but has no "%s" line. Add this '
            "line (from `run-budget report %s%s`): %s" % (t, "Cost %s:" % t if many else "Cost:", t,
                                                           " --label" if many else "", report_line(cfg, t, label=many))
            for t in missing]


def mark_reported(text, cfg, now):
    if not (COST_PLAIN.search(text) or COST_LABEL.search(text)):
        return
    p = _reported_path(cfg)
    rep = _read_json(p, {}) or {}
    for t in _ts_in(text):
        rep[t] = now
    _write_json(p, rep)


# ---------------------------------------------------------------- audit (KGI.md)

def _default_projects(cfg):
    work = cfg.get("WORK_DIR") or os.path.join(cfg.get("_HOME", ""), "factory")
    return os.path.join(cfg.get("_HOME", ""), ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", work))


def _default_replies(cfg):
    if cfg.get("ASK_ME_STATE"):
        return os.path.join(cfg["ASK_ME_STATE"], "channel", "replies.jsonl")
    return ""


def _replies(path):
    out = []
    try:
        lines = Path(path).read_text().splitlines()
    except (OSError, TypeError):
        return out
    for line in lines:
        try:
            r = json.loads(line)
            out.append((float(r["t"]), str(r.get("text") or "")))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def _has_cost_for(text, t):
    return bool(re.search(r"\b%s\b" % re.escape(t), text)) and (
        bool(COST_PLAIN.search(text)) or t in COST_LABEL.findall(text))


def audit(cfg, since, projects_dir=None, reply_log=None):
    since_ts = parse_ts(since) or 0
    projects_dir = str(projects_dir or cfg.get("RB_PROJECTS") or _default_projects(cfg))
    reply_log = reply_log or cfg.get("RB_REPLY_LOG") or _default_replies(cfg)
    all_rows = [r for r in read_rows(cfg, all_rows=True)
                if (parse_ts(r.get("end")) or parse_ts(r.get("start")) or 0) >= since_ts]
    last = OrderedDict()
    for r in all_rows:
        last.pop(r["agent_id"], None)
        last[r["agent_id"]] = r
    replies = _replies(reply_log)
    res = {"since": since, "runs": len(last), "silent_overruns": [], "missing_cost": [], "k1_incomplete": [],
           "k2_checked": 0, "k2_mismatch": [], "k3_lost": [], "k4_wrong_stops": [], "k5_overshoot": []}
    # transcripts: silent overruns (over a hard cap with no stop row)
    files = glob.glob(os.path.join(glob.escape(projects_dir), "*", "subagents", "agent-*.jsonl")) + \
        glob.glob(os.path.join(glob.escape(projects_dir), "*", "subagents", "workflows", "*", "agent-*.jsonl"))
    totals = {}
    for f in sorted(files):
        aid = os.path.basename(f)[len("agent-"):-len(".jsonl")]
        meta = _read_json(f[:-6] + ".meta.json", {}) or {}
        if not meta.get("agentType"):
            continue
        tot = transcript_totals(f)
        first = tot.get("first_ts") or os.path.getmtime(f)
        if first < since_ts:
            continue
        totals[aid] = tot
        row = last.get(aid)
        c = (row or {}).get("caps") or caps(cfg, meta.get("agentType"), meta.get("description"))
        mins = ((tot.get("last_ts") or first) - first) / 60.0
        ctx, tools = tot["context_tokens"], tot["tool_uses"]
        over = (isinstance(ctx, int) and ctx > c["ctx_hard"]) or (isinstance(tools, int) and tools > c["tools_hard"]) \
            or mins > c["min_hard"]
        if over and (not row or row.get("stop_reason") == "completed"):
            res["silent_overruns"].append({"agent_id": aid, "context_tokens": ctx, "tool_uses": tools,
                                           "minutes": round(mins, 1), "row": (row or {}).get("stop_reason", "none")})
    # notifications anywhere in the session transcripts (K2)
    notes = {}
    for f in sorted(glob.glob(os.path.join(glob.escape(projects_dir), "*.jsonl")) + files):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                notifications_from_lines(fh, notes)
        except OSError:
            continue
    for aid, r in last.items():
        bad = [k for k in FIELDS if r.get(k) in (None, "", "unknown")]
        tok = (r.get("tokens") or {}).get("total")
        if tok in (None, "", "unknown"):
            bad.append("tokens")
        if r.get("t") in ("none", None):
            bad.append("t")
        tt = totals.get(aid)
        if tt and isinstance(tt["cumulative"]["total"], int) and isinstance(tok, int) and tok != tt["cumulative"]["total"]:
            bad.append("tokens!=transcript")
        if bad:
            res["k1_incomplete"].append({"agent_id": aid, "fields": sorted(set(bad))})
        n = notes.get(aid)
        if n and "subagent_tokens" in n:
            res["k2_checked"] += 1
            if r.get("context_tokens") != n["subagent_tokens"] or r.get("tool_uses") != n.get("tool_uses"):
                res["k2_mismatch"].append({"agent_id": aid, "logged": [r.get("context_tokens"), r.get("tool_uses")],
                                           "notification": [n["subagent_tokens"], n.get("tool_uses")]})
        reason = r.get("stop_reason") or "unknown"
        if reason != "completed":
            start = parse_ts(r.get("start")) or 0
            wt = r.get("worktree")
            kept = False
            if wt and wt != "none" and os.path.isdir(wt):
                try:
                    ct = subprocess.run(["git", "-C", wt, "log", "-1", "--format=%ct"], capture_output=True,
                                        text=True, timeout=5).stdout.strip()
                    kept = bool(ct) and float(ct) >= start
                except (OSError, subprocess.SubprocessError, ValueError):
                    kept = False
                pm = os.path.join(wt, "PARTIAL.md")
                kept = kept or (os.path.exists(pm) and os.path.getmtime(pm) >= start)
            end = parse_ts(r.get("end")) or 0
            named = any(ts >= end and _has_cost_for(tx, r.get("t") or "") and reason in tx for ts, tx in replies)
            if not (kept and named):
                res["k3_lost"].append({"agent_id": aid, "stop_reason": reason, "work_kept": kept, "reason_named": named})
        c = r.get("caps") or caps(cfg, r.get("agent_type"), r.get("description"))
        cap = re.sub(r"^killed:", "", reason)
        val, lim = {"cap:tools": (r.get("tool_uses"), c["tools_hard"]),
                    "cap:context": (r.get("context_tokens"), c["ctx_hard"]),
                    "cap:minutes": (r.get("minutes"), c["min_hard"])}.get(cap, (None, None))
        if isinstance(val, (int, float)) and val > lim * 1.10:
            res["k5_overshoot"].append({"agent_id": aid, "stop_reason": reason, "value": val, "cap": lim})
    seen_stall = set()
    for r in all_rows:
        reason = r.get("stop_reason") or ""
        if reason.startswith("stall:"):
            seen_stall.add(r["agent_id"])
        elif reason == "completed" and r["agent_id"] in seen_stall and r["agent_id"] not in res["k4_wrong_stops"]:
            res["k4_wrong_stops"].append(r["agent_id"])
    by_t = OrderedDict()
    for r in last.values():
        t = r.get("t")
        if t and t != "none":
            by_t[t] = max(by_t.get(t, 0), parse_ts(r.get("end")) or 0)
    for t, end in by_t.items():
        if not any(ts >= end and _has_cost_for(tx, t) for ts, tx in replies):
            res["missing_cost"].append(t)
    res["kgi_met"] = not res["silent_overruns"] and not res["missing_cost"] and res["runs"] >= 20
    return res


def format_audit(res):
    n = res["runs"]
    lines = ["run-budget audit since %s: %d runs" % (res["since"], n)]
    verdict = "KGI met" if res["kgi_met"] else "KGI not met"
    why = []
    if n < 20:
        why.append("fewer than 20 runs")
    if res["silent_overruns"]:
        why.append("%d silent overruns" % len(res["silent_overruns"]))
    if res["missing_cost"]:
        why.append("%d T reports without Cost line" % len(res["missing_cost"]))
    lines.append("KGI: silent overruns %d, finished T reports without a Cost line %d -> %s%s" % (
        len(res["silent_overruns"]), len(res["missing_cost"]), verdict, (" (" + ", ".join(why) + ")") if why else ""))
    lines.append("K1 complete rows: %d/%d" % (n - len(res["k1_incomplete"]), n))
    lines.append("K2 notification match: %d/%d" % (res["k2_checked"] - len(res["k2_mismatch"]), res["k2_checked"]))
    stopped = len(res["k3_lost"])
    lines.append("K3 stopped runs without kept work or named reason: %d" % stopped)
    lines.append("K4 wrong stops (stall then resumed to completed): %d (target <= 1 in 20)" % len(res["k4_wrong_stops"]))
    lines.append("K5 capped runs over cap +10%%: %d" % len(res["k5_overshoot"]))
    for key in ("silent_overruns", "missing_cost", "k1_incomplete", "k2_mismatch", "k3_lost", "k4_wrong_stops",
                "k5_overshoot"):
        for item in res[key]:
            lines.append("  %s: %s" % (key, json.dumps(item, ensure_ascii=False)))
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

USAGE = """usage: run-budget report T<n> [--label]
       run-budget audit --since YYYY-MM-DD [--projects DIR] [--replies FILE]
       run_budget.py            (hook mode: event JSON on stdin)"""


def _opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        try:
            cfg = load_config()
        except Exception:  # noqa: BLE001
            return 0
        out = run_hook(sys.stdin.read(), cfg)
        if out:
            print(out)
        return 0
    cfg = load_config()
    if argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] == "report" and len(argv) >= 2:
        print(report_line(cfg, argv[1], label="--label" in argv))
        return 0
    if argv[0] == "audit" and _opt(argv, "--since"):
        res = audit(cfg, _opt(argv, "--since"), _opt(argv, "--projects"), _opt(argv, "--replies"))
        print(format_audit(res))
        return 0 if res["kgi_met"] else 1
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
