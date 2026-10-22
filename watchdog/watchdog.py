#!/usr/bin/env python3
"""Watchdog for the factory foreman.

Every --interval seconds (default 15), looks for a process whose command
line contains 'server:whatsapp' (not the watchdog itself) and remembers its
PID. If several match, the lowest PID is taken as the foreman. Messages go
via the kit's ask-me.py --tell:
- running -> stopped: 'Factory foreman stopped at YYYY-MM-DD HH:MM' (TZ)
- stopped -> running: 'Factory foreman is running again'
- running at two consecutive checks with different PIDs (a restart between
  checks): 'Factory foreman restarted at YYYY-MM-DD HH:MM' (TZ)
- the foreman runs but its channel has not read the chat for --quiet-after
  seconds (default 600; the channel writes <ASK_ME_STATE>/channel.heartbeat
  after every read): 'Factory channel quiet: ...', and once it reads again
  'Factory channel is reading messages again'
Nothing is sent at startup or while the state and PID stay the same.

It also watches Codex runs (T18), from the rollout files Codex writes for every session (codex exec, the
VS Code/Desktop client and the TUI alike), see CodexWatch below:
- 'Codex finished: <repo> - <first line of the last message>' when a turn ends (task_complete)
- 'Codex idle <N> min after finishing: <repo>' once, when a finished session stays quiet N minutes
- 'Codex stopped without finishing: <repo>' when a turn has no task_complete, its rollout has been quiet
  N minutes and no codex process holds it open (or works in its folder)
- 'Codex interrupted (<reason>): <repo>' on turn_aborted
One message per transition, kept in <ASK_ME_STATE>/codex-watch/state.json.
TZ, ASK_ME_STATE, CODEX_IDLE_MIN and CODEX_SESSIONS come from factory.env. Standard library only.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import kitenv  # noqa: E402

CFG = kitenv.load()
PATTERN = os.environ.get("WATCHDOG_PATTERN", "server:whatsapp")
SELF_MARKER = "watchdog.py"
ASK_ME = str(Path(__file__).resolve().parents[1] / "scripts" / "ask-me.py")
ZONE = ZoneInfo(CFG["TZ"] or "UTC")
STATE = CFG["ASK_ME_STATE"]
QUIET_AFTER = 600


def list_processes():
    """Return (pid, command) pairs for all processes (macOS ps)."""
    out = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True, text=True, check=True,
    ).stdout
    procs = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        procs.append((pid, parts[1] if len(parts) > 1 else ""))
    return procs


def send_message(text):
    """Send one message through ask-me.py --tell. Raises on failure."""
    subprocess.run(
        [sys.executable, ASK_ME, "--from", "watchdog", "--tell", text],
        check=True, timeout=120,
    )


def now():
    return datetime.datetime.now(ZONE)


def heartbeat_age(state=None):
    """Seconds since the channel last read the chat, or None when it never wrote a heartbeat."""
    try:
        t = float((Path(state or STATE) / "channel.heartbeat").read_text().strip())
    except (OSError, ValueError):
        return None
    return max(0.0, time.time() - t)


def foreman_pid(procs, pattern=PATTERN, self_marker=SELF_MARKER):
    """PID of the foreman in (pid, command) pairs, or None if not running.

    A process counts if its command line contains pattern and not
    self_marker (the watchdog itself). If several match, the lowest PID wins,
    so the choice is deterministic.
    """
    pids = [pid for pid, cmd in procs
            if pattern in cmd and self_marker not in cmd]
    return min(pids) if pids else None


def foreman_running(procs, pattern=PATTERN, self_marker=SELF_MARKER):
    """True if the foreman is among the (pid, command) pairs."""
    return foreman_pid(procs, pattern, self_marker) is not None


class Watchdog:
    def __init__(self, list_processes=list_processes, send=send_message,
                 clock=now, sleep=time.sleep, log=None, heartbeat=heartbeat_age, quiet_after=QUIET_AFTER,
                 codex=None):
        self.list_processes = list_processes
        self.send = send
        self.clock = clock
        self.sleep = sleep
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.running = None  # unknown until the first check
        self.pid = None      # foreman PID at the last check (None if stopped)
        self.heartbeat = heartbeat
        self.quiet_after = quiet_after
        self.quiet = False   # the channel has been quiet longer than quiet_after
        self.codex = codex   # CodexWatch, or None

    def current_pid(self):
        return foreman_pid(self.list_processes())

    def is_running(self):
        return self.current_pid() is not None

    def _stamp(self, ago=0):
        t = self.clock().astimezone(ZONE) - datetime.timedelta(seconds=ago)
        return t.strftime("%Y-%m-%d %H:%M")

    def _deliver(self, text):
        try:
            self.send(text)
        except Exception as e:
            self.log(f"watchdog: sending failed ({text!r}): {e}")
        return text

    def check_quiet(self, startup):
        """While the foreman runs: one message when its channel goes quiet, one when it reads again."""
        if not self.running:
            self.quiet = False
            return None
        try:
            age = self.heartbeat()
        except Exception as e:
            self.log(f"watchdog: heartbeat check failed: {e}")
            return None
        if age is None:
            return None
        quiet = age > self.quiet_after
        was, self.quiet = self.quiet, quiet
        if startup or quiet == was:
            return None
        if quiet:
            return self._deliver(f"Factory channel quiet: no messages read since {self._stamp(age)} "
                                 f"({int(age // 60)} min). Check Beeper Desktop and the foreman terminal")
        return self._deliver("Factory channel is reading messages again")

    def check_once(self):
        """Check the foreman once (a message only on a state or PID change), then the Codex runs.

        Returns the foreman message or None.
        """
        text = self.check_foreman()
        if self.codex is not None:
            try:
                self.codex.check()
            except Exception as e:  # never let the Codex watch stop the foreman watch
                self.log(f"watchdog: codex check failed: {e}")
        return text

    def check_foreman(self):
        try:
            now_pid = self.current_pid()
        except Exception as e:  # ps failed: keep the previous state
            self.log(f"watchdog: process list failed: {e}")
            return None

        now_running = now_pid is not None
        previous, previous_pid = self.running, self.pid
        self.running, self.pid = now_running, now_pid
        quiet_text = self.check_quiet(startup=previous is None)
        if previous is None:
            return None  # startup: never send

        if previous and now_running:
            if previous_pid == now_pid:
                return quiet_text
            text = f"Factory foreman restarted at {self._stamp()}"
        elif previous == now_running:
            return quiet_text
        elif now_running:
            text = "Factory foreman is running again"
        else:
            text = f"Factory foreman stopped at {self._stamp()}"
        try:
            self.send(text)
        except Exception as e:
            self.log(f"watchdog: sending failed ({text!r}): {e}")
        return text

    def run(self, interval=15, iterations=None):
        n = 0
        while iterations is None or n < iterations:
            self.check_once()
            n += 1
            if iterations is not None and n >= iterations:
                break
            self.sleep(interval)


# --- Codex runs (T18) -------------------------------------------------------------------------------------------
# Codex writes one rollout file per session: <CODEX_SESSIONS>/YYYY/MM/DD/rollout-<time>-<id>.jsonl. Its first
# record is session_meta (id, cwd, source); turns are event_msg records task_started, task_complete (with
# last_agent_message) and turn_aborted (reason interrupted|replaced|review_ended|budget_limited)
# (openai/codex 86e17e98 codex-rs/protocol/src/protocol.rs:1519,1528,4394,6260). Subagent rollouts (source
# {"subagent": ...}: guardian approval reviews, thread_spawn workers) are never told themselves; their activity
# counts as their parent's. Turns imported by Codex Desktop (turn ids "external-import-*") are not runs.

TURN_EVENTS = ("task_started", "task_complete", "turn_aborted")
ABORT_SETTLE = 30        # s: a "replaced" turn is followed at once by a new turn; tell only a lasting abort
HOOK_SETTLE = 20         # s: ask_me_stop runs beside the Stop hook and may send the turn back
RECENT = 24 * 3600       # s: only rollouts changed in the last 24 h are read
FORGET = 7 * 24 * 3600   # s: told transitions are kept this long (events older than RECENT are never told)
HEAD_MAX = 4 << 20       # bytes: longest session_meta line read
CHUNK = 1 << 20          # bytes per read
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
    """The first non-empty line of text, whitespace collapsed, secrets redacted, at most `limit` characters."""
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            line = SECRET_RE.sub("[redacted]", line)
            return line if len(line) <= limit else line[:limit].rstrip() + "…"
    return ""


def codex_processes():
    """Running codex processes: [{"pid", "cwd", "open" (rollout files it holds open), "host"}], or None when
    they can't be listed. "host" marks `codex app-server`/`exec-server`, which serve many sessions from one
    folder: they count only through the rollouts they hold open, never through their cwd."""
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=20)
        ls = subprocess.run(["lsof", "-nP", "-w", "-c", "codex", "-Fpfn"], capture_output=True, text=True,
                            timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if ls.returncode not in (0, 1) or (ls.returncode == 1 and ls.stdout.strip() and "p" not in ls.stdout[:1]):
        return None
    cmds = {}
    for line in (ps.stdout or "").splitlines():
        parts = line.strip().split(None, 1)
        if parts and parts[0].isdigit():
            cmds[int(parts[0])] = parts[1] if len(parts) > 1 else ""
    procs, cur, fd = [], None, None
    for line in (ls.stdout or "").splitlines():
        tag, val = line[:1], line[1:]
        if tag == "p" and val.isdigit():
            cmd = cmds.get(int(val), "")
            cur = {"pid": int(val), "cwd": None, "open": set(),
                   "host": bool(re.search(r"\s(app-server|exec-server|codex-code-mode-host)\b", " " + cmd))}
            procs.append(cur)
        elif tag == "f":
            fd = val
        elif tag == "n" and cur is not None:
            if fd == "cwd":
                cur["cwd"] = val
            elif os.path.basename(val).startswith("rollout-") and val.endswith(".jsonl"):
                cur["open"].add(val)
    return procs


def _epoch(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class Rollout:
    """What the watcher knows about one rollout file, read incrementally."""

    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.ino = None
        self.meta = None          # {"id", "cwd", "sub", "parent"}
        self.last = None          # last turn event: {"type", "turn", "reason", "message", "time"}

    def read(self, st):
        if self.ino != st.st_ino or st.st_size < self.offset:
            self.__init__(self.path)
            self.ino = st.st_ino
        if st.st_size == self.offset:
            return
        with open(self.path, "rb") as f:
            if self.meta is None and self.offset == 0:
                head = f.readline(HEAD_MAX)  # session_meta: about 23 KB on Codex 0.163
                if not head.endswith(b"\n"):
                    if len(head) < HEAD_MAX:
                        return           # first line still being written
                    self._meta(b"")      # unreadably long: id from the file name, cwd unknown
                    while head and not head.endswith(b"\n"):
                        head = f.readline(HEAD_MAX)
                    self.offset = f.tell()
                else:
                    self.offset = len(head)
                    self._meta(head)
            f.seek(self.offset)
            rest = b""
            while True:                  # 1 MB at a time: big rollouts (images) never sit in memory whole
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                lines = (rest + chunk).split(b"\n")
                rest = lines.pop()
                for line in lines:
                    if b'"event_msg"' in line and (b"task_" in line or b"turn_aborted" in line):
                        self._event(line)
                self.offset += len(chunk)
            self.offset -= len(rest)     # an unfinished last line is read again next time

    def _meta(self, line):
        try:
            rec = json.loads(line)
        except ValueError:
            rec = {}
        p = rec.get("payload") if isinstance(rec, dict) and rec.get("type") == "session_meta" else None
        p = p if isinstance(p, dict) else {}
        src = p.get("source")
        sub = src.get("subagent") if isinstance(src, dict) else None
        parent = p.get("parent_thread_id")
        if not parent and isinstance(sub, dict):
            spawn = sub.get("thread_spawn")
            parent = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", self.path)
        self.meta = {"id": str(p.get("id") or (m.group(1) if m else self.path)), "cwd": str(p.get("cwd") or ""),
                     "exec": src == "exec",
                     "sub": isinstance(src, dict) and "subagent" in src, "parent": parent}

    def _event(self, line):
        try:
            rec = json.loads(line)
        except ValueError:
            return
        p = rec.get("payload") if isinstance(rec, dict) else None
        if not isinstance(p, dict) or p.get("type") not in TURN_EVENTS:
            return
        turn = str(p.get("turn_id") or rec.get("timestamp") or "")
        if turn.startswith("external-import"):
            return
        self.last = {"type": p["type"], "turn": turn, "reason": str(p.get("reason") or ""),
                     "message": first_line(p.get("last_agent_message")) if p["type"] == "task_complete" else "",
                     "time": _epoch(rec.get("timestamp"))}


class CodexWatch:
    """Tells the owner when a Codex run finishes, stops, is interrupted or sits idle after finishing (T18)."""

    def __init__(self, sessions, idle_min=10, state_dir=None, procs=codex_processes, clock=time.time,
                 send=None, log=None):
        self.sessions = str(sessions)
        self.idle_min = idle_min
        self.idle = idle_min * 60
        self.dir = Path(state_dir or STATE) / "codex-watch"
        self.procs = procs
        self.clock = clock
        self.send = send or send_message
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.rollouts = {}
        self.told, self.baseline = self._load()
        self.started = clock()   # transitions long before this start are recorded, never sent (no burst)
        self.failures = {}       # key -> failed sends; a failed message is tried again up to 3 times
        self.saved = None if self.baseline else dict(self.told)

    # state: {"told": {key: time}}; a missing or unreadable file means the first run, which only takes a baseline
    def _load(self):
        try:
            told = json.loads((self.dir / "state.json").read_text())["told"]
            if isinstance(told, dict):
                return {str(k): float(v) for k, v in told.items()}, False
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return {}, True

    def _save(self):
        now = self.clock()
        kept = {k: v for k, v in self.told.items() if now - v < FORGET}
        if kept == self.saved:
            return
        self.told = kept
        try:
            self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = self.dir / "state.json.tmp"
            tmp.write_text(json.dumps({"told": self.told}))
            tmp.replace(self.dir / "state.json")
            self.saved = dict(kept)
        except OSError as e:
            self.log(f"watchdog: codex state not saved: {e}")

    def seen_paths(self):
        return sorted(self.rollouts)

    def _scan(self, now):
        found = set()
        try:
            years = os.scandir(self.sessions)
        except OSError:
            return
        for d in _subdirs(years, 3):
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                if not (e.name.startswith("rollout-") and e.name.endswith(".jsonl")):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                if now - st.st_mtime > RECENT:
                    continue
                r = self.rollouts.get(e.path) or Rollout(e.path)
                try:
                    r.read(st)
                except OSError as err:
                    self.log(f"watchdog: codex rollout unreadable: {e.name}: {err}")
                    continue
                r.mtime = st.st_mtime
                self.rollouts[e.path] = r
                found.add(e.path)
        for gone in set(self.rollouts) - found:
            del self.rollouts[gone]

    def _alive(self, r, procs):
        paths = {r.path, os.path.realpath(r.path)}
        cwd = r.meta["cwd"]
        for p in procs:
            if paths & set(p.get("open") or ()):
                return True
        for p in procs:
            if cwd and not p.get("host") and not p.get("open") and p.get("cwd") in (cwd, os.path.realpath(cwd)):
                return True
        return False

    def _tell(self, key, text, now, stale=False):
        """Send text once for key. A stale transition (long before this watchdog started, or an event older than
        RECENT in a resumed session) and everything at the first-start baseline is only recorded."""
        if key in self.told:
            return None
        self.told[key] = now
        if self.baseline or stale:
            return None
        try:
            self.send(text)
        except Exception as e:
            n = self.failures[key] = self.failures.get(key, 0) + 1
            self.log(f"watchdog: sending failed ({text!r}, try {n} of 3): {e}")
            if n < 3:
                del self.told[key]       # tried again next check
            return None
        return text

    def check(self):
        """Read the rollouts and the Stop hook records once; send one message per new transition."""
        now = self.clock()
        self._scan(now)
        mains = [r for r in self.rollouts.values() if r.meta and not r.meta["sub"]]
        latest = {}  # session id -> latest activity of the session and its subagents
        for r in self.rollouts.values():
            if r.meta:
                for sid in (r.meta["id"], r.meta["parent"]):
                    if sid:
                        latest[sid] = max(latest.get(sid, 0), r.mtime)
        sent, procs = [], False
        for r in sorted(mains, key=lambda r: r.path):
            ev, sid = r.last, r.meta["id"]
            if ev is None:
                continue
            repo = os.path.basename(r.meta["cwd"].rstrip("/")) or "?"
            active = max(latest.get(sid, 0), r.mtime)
            quiet = now - active
            stale = active < self.started - 2 * self.idle or (ev["time"] or now) < now - RECENT
            base = f"{sid}:{ev['turn']}"
            if ev["type"] == "task_complete":
                sent.append(self._tell(base + ":finished", _finished(repo, ev["message"]), now, stale))
                if quiet >= self.idle and not r.meta["exec"]:   # a codex exec run ends with its process
                    sent.append(self._tell(base + ":idle", f"Codex idle {self.idle_min} min after finishing: {repo}",
                                           now, stale))
            elif ev["type"] == "turn_aborted":
                if quiet >= ABORT_SETTLE:
                    reason = ev["reason"].replace("_", " ") or "unknown"
                    sent.append(self._tell(base + ":aborted", f"Codex interrupted ({reason}): {repo}", now, stale))
            elif quiet >= self.idle and base + ":stopped" not in self.told:
                if procs is False:
                    procs = self.procs()
                    if procs is None:
                        self.log("watchdog: codex process list failed; no stop alerts this round")
                if procs is not None and not self._alive(r, procs):
                    sent.append(self._tell(base + ":stopped", f"Codex stopped without finishing: {repo}", now,
                                           stale))
        sent += self._hook_records(now)
        self.baseline = False
        self._save()
        return [s for s in sent if s]

    def _hook_records(self, now):
        """Stop hook records (hooks/codex_watch_stop.py). A session with a rollout is told from the rollout;
        the hook covers sessions without one (codex exec --ephemeral, another CODEX_HOME)."""
        d = self.dir / "stops"
        try:
            files = sorted(d.glob("*.json"))
        except OSError:
            return []
        known = {r.meta["id"] for r in self.rollouts.values() if r.meta}
        sent = []
        for f in files:
            try:
                rec = json.loads(f.read_text())
                sid, turn, t = str(rec["session_id"]), str(rec.get("turn_id") or ""), float(rec["time"])
            except (OSError, ValueError, KeyError, TypeError):
                _unlink(f)
                continue
            if sid in known or now - t > RECENT:
                if sid not in known or now - t > 3600:
                    _unlink(f)
                continue
            if now - t < HOOK_SETTLE:
                continue
            repo = os.path.basename(str(rec.get("cwd") or "").rstrip("/")) or "?"
            key = f"{sid}:{turn}:finished"
            sent.append(self._tell(key, _finished(repo, first_line(rec.get("first_line"))), now))
            if key in self.told:
                _unlink(f)
        return sent


def _finished(repo, line):
    return f"Codex finished: {repo} - {line}" if line else f"Codex finished: {repo}"


def _unlink(path):
    try:
        path.unlink()
    except OSError:
        pass


def _subdirs(it, depth):
    """Directories `depth` levels below the scandir iterator `it` (YYYY/MM/DD)."""
    with it:
        dirs = [e.path for e in it if e.is_dir()]
    for _ in range(depth - 1):
        nxt = []
        for d in dirs:
            try:
                with os.scandir(d) as sub:
                    nxt += [e.path for e in sub if e.is_dir()]
            except OSError:
                continue
        dirs = nxt
    return dirs


def codex_watch_from_config(cfg):
    try:
        idle = int(str(cfg.get("CODEX_IDLE_MIN") or "10").strip())
        if idle < 1:
            raise ValueError
    except ValueError:
        idle = 10
    sessions = cfg.get("CODEX_SESSIONS") or os.path.expanduser("~/.codex/sessions")
    return CodexWatch(sessions=sessions, idle_min=idle, state_dir=cfg.get("ASK_ME_STATE") or STATE)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interval", type=float, default=15,
                   help="seconds between checks (default 15)")
    p.add_argument("--quiet-after", type=float, default=QUIET_AFTER,
                   help="seconds without a channel read before a quiet alert (default 600)")
    p.add_argument("--once", action="store_true",
                   help="check once, print the state and exit (never sends)")
    p.add_argument("--no-codex", action="store_true", help="do not watch Codex runs")
    args = p.parse_args(argv)

    codex = None if args.no_codex or args.once else codex_watch_from_config(CFG)
    wd = Watchdog(quiet_after=args.quiet_after, codex=codex)
    if args.once:
        wd.check_once()
        if wd.running:
            print(f"running (pid {wd.pid})")
        else:
            print("stopped" if wd.running is False else "unknown")
        return 0
    wd.log(f"watchdog: started, interval {args.interval}s"
           + (f"; Codex rollouts in {codex.sessions}, idle after {codex.idle_min} min" if codex else ""))
    try:
        wd.run(interval=args.interval)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
