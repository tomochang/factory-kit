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
TZ and ASK_ME_STATE come from factory.env. Standard library only.
"""
import argparse
import datetime
import os
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
                 clock=now, sleep=time.sleep, log=None, heartbeat=heartbeat_age, quiet_after=QUIET_AFTER):
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
        """Check once; send a message only on a state or PID change.

        Returns the message or None.
        """
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


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interval", type=float, default=15,
                   help="seconds between checks (default 15)")
    p.add_argument("--quiet-after", type=float, default=QUIET_AFTER,
                   help="seconds without a channel read before a quiet alert (default 600)")
    p.add_argument("--once", action="store_true",
                   help="check once, print the state and exit (never sends)")
    args = p.parse_args(argv)

    wd = Watchdog(quiet_after=args.quiet_after)
    if args.once:
        wd.check_once()
        if wd.running:
            print(f"running (pid {wd.pid})")
        else:
            print("stopped" if wd.running is False else "unknown")
        return 0
    wd.log(f"watchdog: started, interval {args.interval}s")
    try:
        wd.run(interval=args.interval)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
