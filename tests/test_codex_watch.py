"""T18: the watchdog also watches Codex runs (SPEC R1-R3, R6; KGI replay).

Every case replays a rollout fixture made from a real run on the owner's Mac (record types, timestamps, turn ids
and session_meta only; no prompt text, and last_agent_message replaced by a made-up line):

- t9-vscode-main.jsonl    T9, VS Code client: last turn task_complete 2026-10-21T18:58:01Z, then 6 h idle
- t9-exec-stopped.jsonl   T9, codex exec: task_started 17:54:55Z, last record 17:55:26Z, no task_complete
- t9-exec-subagent.jsonl  T9, the second "exec run": a thread_spawn subagent of t9-exec-stopped (17:55:24Z)
- t9-thread-spawn.jsonl   T9, a thread_spawn subagent of the VS Code session
- t9-guardian.jsonl       T9, guardian (approval reviewer) rollout of the VS Code session
- aborted-vscode.jsonl    2026-09-30, VS Code client: turn_aborted reason=interrupted
- desktop-import.jsonl    2026-10-22, Codex Desktop imported history (turn ids external-import-turn-*)
- parallel-app.jsonl      2026-10-22, VS Code client in another repo (time-shifted to run beside T9)

The watcher reads the rollouts from a temporary sessions folder; processes, clock and sender are fakes.
Nothing here calls ps, lsof or ask-me.
"""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

_saved = {k: os.environ.get(k) for k in ("TZ", "FACTORY_ENV", "ASK_ME_STATE")}
os.environ.update(TZ="Asia/Tokyo", FACTORY_ENV="/nonexistent/factory.env", ASK_ME_STATE=tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "watchdog"))
import watchdog  # noqa: E402
from watchdog import CodexWatch, first_line  # noqa: E402

for _k, _v in _saved.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v

FIX = Path(__file__).resolve().parent / "fixtures" / "codex"
T9 = "/private/tmp/t9-inbox-zero"
T9_DONE = "Codex finished: t9-inbox-zero - T9 draft-reply learnings written; tests pass."


def epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


class Replay:
    """Writes a fixture's records into <sessions>/YYYY/MM/DD/rollout-...-<id>.jsonl up to a time, as Codex would."""

    def __init__(self, sessions, name, shift=0.0):
        self.records = [json.loads(l) for l in (FIX / name).read_text().splitlines() if l.strip()]
        self.shift = shift
        meta = self.records[0]["payload"]
        self.id = meta["id"]
        start = datetime.fromtimestamp(epoch(meta["timestamp"]) + shift, timezone.utc)
        self.path = Path(sessions) / start.strftime("%Y/%m/%d") / (
            "rollout-" + start.strftime("%Y-%m-%dT%H-%M-%S") + f"-{self.id}.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def ts(self, rec):
        return epoch(rec["timestamp"]) + self.shift

    def upto(self, t):
        recs = [r for r in self.records if self.ts(r) <= t]
        if not recs:
            return self
        with open(self.path, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        last = self.ts(recs[-1])
        os.utime(self.path, (last, last))
        return self


class Procs:
    """Fake codex process list: dicts like codex_processes() returns, or None (lsof failed)."""

    def __init__(self, procs=()):
        self.procs = None if procs is None else list(procs)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return None if self.procs is None else list(self.procs)


class World:
    def __init__(self, tmp_path, procs=None, idle_min=10):
        self.sessions = tmp_path / "sessions"
        self.sessions.mkdir()
        self.state = tmp_path / "state"
        self.now = 0.0
        self.sent = []
        self.logs = []
        self.procs = procs if procs is not None else Procs()
        self.replays = []
        self.make(idle_min)

    def make(self, idle_min=10):
        self.watch = CodexWatch(sessions=str(self.sessions), idle_min=idle_min, state_dir=str(self.state),
                                procs=self.procs, clock=lambda: self.now, send=self.sent.append,
                                log=self.logs.append)

    def add(self, name, shift=0.0):
        r = Replay(self.sessions, name, shift)
        self.replays.append(r)
        return r

    def at(self, iso_or_epoch):
        self.now = epoch(iso_or_epoch) if isinstance(iso_or_epoch, str) else iso_or_epoch
        for r in self.replays:
            r.upto(self.now)
        before = len(self.sent)
        self.watch.check()
        return self.sent[before:]

    def run(self, start, end, step=15):
        """Check every `step` seconds from start to end; return everything sent."""
        t, out = epoch(start), []
        while t <= epoch(end):
            out += self.at(t)
            t += step
        return out


def started(w, iso):
    """First check: the watcher starts with no state file, so it only takes a baseline (sends nothing)."""
    assert w.at(iso) == []


# --- KGI case 1: T9 in the VS Code client, finished at 18:58:01Z and then idle for 6 h

def test_t9_vscode_each_finish_is_told_once_then_idle_once_and_nothing_for_6_hours(tmp_path):
    w = World(tmp_path)
    w.add("t9-vscode-main.jsonl")
    w.add("t9-thread-spawn.jsonl")
    w.add("t9-guardian.jsonl")
    started(w, "2026-10-21T18:30:00Z")
    sent = w.run("2026-10-21T18:30:15Z", "2026-10-21T18:58:00Z")
    assert sent == [T9_DONE]                                   # first turn 18:51:18; the 3 min gap is not idle
    assert w.run("2026-10-21T18:58:02Z", "2026-10-21T18:58:16Z") == [T9_DONE]   # within one interval
    assert w.run("2026-10-21T18:58:30Z", "2026-10-21T19:08:00Z") == []          # < 10 min quiet
    idle = w.run("2026-10-21T19:08:15Z", "2026-10-21T19:09:01Z")                # <= N+1 min after finishing
    assert idle == ["Codex idle 10 min after finishing: t9-inbox-zero"]
    assert w.run("2026-10-21T19:09:15Z", "2026-10-22T00:58:01Z", step=60) == []  # once, not every check


def test_idle_minutes_come_from_the_setting(tmp_path):
    w = World(tmp_path, idle_min=3)
    w.add("t9-vscode-main.jsonl")
    started(w, "2026-10-21T18:54:30Z")
    assert w.at("2026-10-21T18:58:10Z") == [T9_DONE]
    assert w.at("2026-10-21T19:01:00Z") == []
    assert w.at("2026-10-21T19:01:15Z") == ["Codex idle 3 min after finishing: t9-inbox-zero"]


# --- KGI case 2: the T9 exec run that stopped silently at 17:55Z (and its subagent, the second "run")

def test_t9_exec_run_that_stopped_silently_is_told_once_within_n_plus_1_minutes(tmp_path):
    w = World(tmp_path)
    w.add("t9-exec-stopped.jsonl")
    w.add("t9-exec-subagent.jsonl")
    started(w, "2026-10-21T17:54:50Z")
    assert w.run("2026-10-21T17:55:05Z", "2026-10-21T18:05:20Z") == []           # quiet < 10 min: still working
    sent = w.run("2026-10-21T18:05:35Z", "2026-10-21T18:06:27Z")
    assert sent == ["Codex stopped without finishing: t9-inbox-zero"]           # one message for the run
    assert w.run("2026-10-21T18:06:40Z", "2026-10-21T20:00:00Z", step=60) == []


def test_the_t9_stop_is_seen_although_a_vscode_session_works_in_the_same_folder(tmp_path):
    # T9 for real: the VS Code session in the same folder started at 17:59Z, hosted by `codex app-server`
    host = {"pid": 21219, "cwd": "/", "open": set(), "host": True}
    w = World(tmp_path, procs=Procs([host]))
    w.add("t9-exec-stopped.jsonl")
    w.add("t9-exec-subagent.jsonl")
    vs = w.add("t9-vscode-main.jsonl", shift=-(epoch("2026-10-21T18:37:26Z") - epoch("2026-10-21T18:00:00Z")))
    host["open"] = {str(vs.path)}
    started(w, "2026-10-21T17:54:50Z")
    sent = w.run("2026-10-21T17:55:05Z", "2026-10-21T18:06:27Z")
    assert sent == ["Codex stopped without finishing: t9-inbox-zero"]


def test_a_process_with_the_same_cwd_keeps_an_unfinished_run_working(tmp_path):
    w = World(tmp_path, procs=Procs([{"pid": 54524, "cwd": T9, "open": set(), "host": False}]))
    w.add("t9-exec-stopped.jsonl")
    started(w, "2026-10-21T17:54:50Z")
    assert w.run("2026-10-21T17:55:05Z", "2026-10-21T19:00:00Z", step=60) == []


# --- KGI case: a long quiet run whose process is still alive (#32634, #93672) is never "stopped"

def test_long_quiet_run_with_its_process_alive_is_working_however_long_it_is_quiet(tmp_path):
    procs = Procs()
    w = World(tmp_path, procs=procs)
    r = w.add("t9-exec-stopped.jsonl")
    procs.procs = [{"pid": 777, "cwd": "/", "open": {str(r.path)}, "host": False}]   # holds the rollout open
    started(w, "2026-10-21T17:54:50Z")
    assert w.run("2026-10-21T17:55:05Z", "2026-10-21T21:00:00Z", step=60) == []   # 3 h of quiet tests: no alarm
    procs.procs = []                                                              # then the process dies
    assert w.at("2026-10-21T21:00:15Z") == ["Codex stopped without finishing: t9-inbox-zero"]


def test_unknown_process_state_never_alarms(tmp_path):
    w = World(tmp_path, procs=Procs(None))
    w.add("t9-exec-stopped.jsonl")
    started(w, "2026-10-21T17:54:50Z")
    assert w.run("2026-10-21T17:55:05Z", "2026-10-21T18:30:00Z", step=60) == []
    assert any("process" in l for l in w.logs)


def test_processes_are_only_listed_when_an_unfinished_run_is_quiet(tmp_path):
    procs = Procs()
    w = World(tmp_path, procs=procs)
    w.add("t9-vscode-main.jsonl")
    started(w, "2026-10-21T18:30:00Z")
    w.run("2026-10-21T18:30:15Z", "2026-10-21T20:00:00Z", step=60)
    assert procs.calls == 0


# --- KGI case: turn_aborted

def test_interrupted_turn_is_told_once_with_its_reason_and_never_turns_into_idle(tmp_path):
    w = World(tmp_path)
    w.add("aborted-vscode.jsonl")
    started(w, "2026-09-30T06:40:00Z")
    assert w.run("2026-09-30T06:40:15Z", "2026-09-30T06:46:20Z") == ["Codex finished: notes-app - Plan saved."]
    sent = w.run("2026-09-30T06:46:24Z", "2026-09-30T06:47:24Z")
    assert sent == ["Codex interrupted (interrupted): notes-app"]
    assert w.run("2026-09-30T06:47:30Z", "2026-09-30T08:00:00Z", step=60) == []


def test_a_replaced_turn_followed_by_a_new_turn_is_not_an_interruption(tmp_path):
    w = World(tmp_path)
    r = w.add("aborted-vscode.jsonl")
    for rec in r.records:
        if rec["payload"].get("type") == "turn_aborted":
            rec["payload"]["reason"] = "replaced"
    r.records.append({"timestamp": "2026-09-30T06:46:23.300Z", "type": "event_msg",
                      "payload": {"type": "task_started", "turn_id": "next-turn"}})
    started(w, "2026-09-30T06:45:30Z")
    assert w.run("2026-09-30T06:45:45Z", "2026-09-30T06:50:00Z") == []


# --- KGI case: parallel sessions

def test_parallel_sessions_are_told_separately(tmp_path):
    w = World(tmp_path)
    w.add("t9-vscode-main.jsonl")
    # the other repo's session, shifted so its turn ends at 18:56:00Z while T9 is still working
    w.add("parallel-app.jsonl", shift=epoch("2026-10-21T18:56:00Z") - epoch("2026-10-22T08:13:03.499Z"))
    started(w, "2026-10-21T18:54:30Z")
    assert w.run("2026-10-21T18:54:45Z", "2026-10-21T18:56:10Z") == ["Codex finished: notes-app - Fixed the parser."]
    assert w.run("2026-10-21T18:56:25Z", "2026-10-21T18:58:10Z") == [T9_DONE]
    assert w.run("2026-10-21T18:58:25Z", "2026-10-21T19:06:15Z") == ["Codex idle 10 min after finishing: notes-app"]
    assert w.run("2026-10-21T19:06:30Z", "2026-10-21T19:08:15Z") == [
        "Codex idle 10 min after finishing: t9-inbox-zero"]


# --- KGI case: guardian and subagent rollouts

def test_guardian_and_subagent_rollouts_are_never_told_themselves(tmp_path):
    w = World(tmp_path)
    w.add("t9-guardian.jsonl")
    w.add("t9-thread-spawn.jsonl")
    w.add("t9-exec-subagent.jsonl")
    started(w, "2026-10-21T17:55:00Z")
    assert w.run("2026-10-21T17:55:15Z", "2026-10-21T20:00:00Z", step=30) == []


def test_a_working_subagent_keeps_its_finished_parent_from_being_idle(tmp_path):
    w = World(tmp_path)
    w.add("t9-vscode-main.jsonl")
    child = w.add("t9-thread-spawn.jsonl")
    # the background subagent keeps working for 20 min after the parent's turn ended
    child.records.append({"timestamp": "2026-10-21T18:59:00.000Z", "type": "event_msg",
                          "payload": {"type": "task_started", "turn_id": "bg"}})
    for m in range(1, 21):
        child.records.append({"timestamp": f"2026-10-21T19:{m:02d}:00.000Z", "type": "response_item",
                              "payload": {"type": "function_call"}})
    started(w, "2026-10-21T18:57:00Z")
    assert w.run("2026-10-21T18:57:15Z", "2026-10-21T19:29:59Z") == [T9_DONE]
    assert w.run("2026-10-21T19:30:00Z", "2026-10-21T19:31:00Z") == [
        "Codex idle 10 min after finishing: t9-inbox-zero"]


# --- imported history and restarts

def test_codex_desktop_imported_history_is_not_a_run(tmp_path):
    w = World(tmp_path)
    w.add("desktop-import.jsonl")
    started(w, "2026-10-22T01:09:00Z")
    assert w.run("2026-10-22T01:09:15Z", "2026-10-22T02:00:00Z", step=60) == []


def test_first_start_takes_a_baseline_and_a_restart_repeats_nothing(tmp_path):
    w = World(tmp_path)
    w.add("t9-vscode-main.jsonl")
    w.add("t9-exec-stopped.jsonl")
    started(w, "2026-10-21T19:30:00Z")              # both already finished/stopped before the first start
    assert w.run("2026-10-21T19:30:15Z", "2026-10-21T20:00:00Z", step=60) == []
    w.make()                                         # watchdog restarted: state comes from the state file
    assert w.run("2026-10-21T20:00:15Z", "2026-10-21T21:00:00Z", step=60) == []
    assert (w.state / "codex-watch" / "state.json").is_file()


def test_a_transition_while_the_watchdog_was_down_is_told_after_restart(tmp_path):
    w = World(tmp_path)
    w.add("t9-vscode-main.jsonl")
    started(w, "2026-10-21T18:55:00Z")
    w.make()
    assert w.at("2026-10-21T18:59:00Z") == [T9_DONE]


def test_state_file_survives_garbage(tmp_path):
    w = World(tmp_path)
    w.add("t9-vscode-main.jsonl")
    started(w, "2026-10-21T18:55:00Z")
    (w.state / "codex-watch" / "state.json").write_text("{not json")
    w.make()
    assert w.at("2026-10-21T18:59:00Z") == []        # unreadable state = a new baseline, never a flood
    assert w.at("2026-10-21T19:09:00Z") == ["Codex idle 10 min after finishing: t9-inbox-zero"]


def test_only_rollouts_changed_in_the_last_24_hours_are_read(tmp_path):
    w = World(tmp_path)
    w.add("aborted-vscode.jsonl")
    w.add("t9-exec-stopped.jsonl")
    started(w, "2026-10-21T17:54:50Z")
    w.at("2026-10-21T18:10:00Z")
    assert all("aborted" not in str(p) for p in w.watch.seen_paths())
    assert len(w.watch.seen_paths()) == 1


def test_unknown_and_broken_records_are_ignored(tmp_path):
    w = World(tmp_path)
    r = w.add("t9-vscode-main.jsonl")
    started(w, "2026-10-21T18:54:30Z")
    r.upto(epoch("2026-10-21T18:58:05Z"))
    with open(r.path, "a") as f:
        f.write("{broken json\n")
        f.write(json.dumps({"timestamp": "2026-10-21T18:58:06Z", "type": "something_new", "payload": {}}) + "\n")
    w.now = epoch("2026-10-21T18:58:10Z")
    w.watch.check()
    assert w.sent == [T9_DONE]


# --- R2: one line, no secrets

def test_first_line_is_one_short_line_without_secrets():
    assert first_line("Done.\n\nDetails follow") == "Done."
    assert first_line("  \n  All green  \nmore") == "All green"
    assert first_line(None) == ""
    long = first_line("word " * 100)
    assert len(long) <= 101 and long.endswith("…")
    s = first_line("token sk-" + "a" * 40 + " and ghp_" + "b" * 36 + " key=" + "c3" * 24)
    assert first_line("see /private/tmp/t9-inbox-zero/watchdog/watchdog.py") == \
        "see /private/tmp/t9-inbox-zero/watchdog/watchdog.py"            # paths are not secrets
    assert "aaaa" not in s and "bbbb" not in s and "cccc" not in s and "[redacted]" in s


def test_finished_without_a_message_has_no_dash(tmp_path):
    w = World(tmp_path)
    r = w.add("parallel-app.jsonl")
    r.records[-1]["payload"]["last_agent_message"] = None
    started(w, "2026-10-22T08:12:30Z")
    assert w.at("2026-10-22T08:13:10Z") == ["Codex finished: notes-app"]


def test_messages_never_hold_more_than_the_first_line(tmp_path):
    w = World(tmp_path)
    r = w.add("parallel-app.jsonl")
    r.records[-1]["payload"]["last_agent_message"] = "Fixed it.\nprompt text and private details"
    started(w, "2026-10-22T08:12:30Z")
    sent = w.at("2026-10-22T08:13:10Z")
    assert sent == ["Codex finished: notes-app - Fixed it."]


# --- R4: the Stop hook's record

def write_stop(state, **rec):
    d = Path(state) / "codex-watch" / "stops"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rec['session_id']}.{rec['turn_id']}.json").write_text(json.dumps(rec))


def test_stop_hook_record_of_a_session_without_rollout_is_told_once(tmp_path):
    # e.g. `codex exec --ephemeral`, or a CODEX_HOME outside CODEX_SESSIONS: only the hook sees it
    w = World(tmp_path)
    started(w, "2026-10-21T18:00:00Z")
    write_stop(w.state, session_id="eph-1", turn_id="t1", cwd="/private/tmp/t7-wt", first_line="Built.",
               time=epoch("2026-10-21T18:00:05Z"))
    assert w.at("2026-10-21T18:00:10Z") == []                    # settles first (a concurrent hook may continue it)
    assert w.at("2026-10-21T18:00:30Z") == ["Codex finished: t7-wt - Built."]
    assert w.run("2026-10-21T18:00:45Z", "2026-10-21T19:00:00Z", step=60) == []
    assert not list((w.state / "codex-watch" / "stops").iterdir())


def test_stop_hook_and_rollout_for_the_same_turn_tell_once(tmp_path):
    w = World(tmp_path)
    r = w.add("t9-vscode-main.jsonl")
    started(w, "2026-10-21T18:55:00Z")
    turn = r.records[-1]["payload"]["turn_id"]
    write_stop(w.state, session_id=r.id, turn_id=turn, cwd=T9, first_line="T9 draft-reply learnings written; tests pass.",
               time=epoch("2026-10-21T18:58:01Z"))
    assert w.run("2026-10-21T18:58:15Z", "2026-10-21T18:59:30Z") == [T9_DONE]


def test_stop_hook_record_is_ignored_when_the_rollout_shows_the_turn_went_on(tmp_path):
    # ask_me_stop runs beside it and may send the turn back ("block"): the rollout then keeps working
    w = World(tmp_path)
    r = w.add("t9-vscode-main.jsonl")
    started(w, "2026-10-21T18:54:30Z")
    write_stop(w.state, session_id=r.id, turn_id="01a15fd0-9a13-7e50-9ce7-f10b78c7de92", cwd=T9,
               first_line="Should I continue?", time=epoch("2026-10-21T18:55:00Z"))
    assert w.run("2026-10-21T18:55:15Z", "2026-10-21T18:57:00Z") == []
    assert w.at("2026-10-21T18:58:10Z") == [T9_DONE]


def test_check_never_raises(tmp_path):
    w = World(tmp_path)
    w.sessions.rmdir()
    w.at("2026-10-21T18:00:00Z")
    w.at("2026-10-21T18:00:15Z")
    assert w.sent == []


# --- wiring into the watchdog loop (R1) and config (R6)

def test_watchdog_runs_the_codex_watch_each_check_and_survives_its_failure():
    calls = []

    class Boom:
        def check(self):
            calls.append(1)
            raise RuntimeError("boom")

    logs = []
    wd = watchdog.Watchdog(list_processes=lambda: [], send=lambda t: None, sleep=lambda s: None,
                           log=logs.append, heartbeat=lambda: None, codex=Boom())
    wd.check_once()
    wd.check_once()
    assert len(calls) == 2 and any("codex" in l.lower() for l in logs)


def test_config_defaults(monkeypatch, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    import kitenv
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FACTORY_ENV", str(tmp_path / "none.env"))
    monkeypatch.delenv("CODEX_IDLE_MIN", raising=False)
    monkeypatch.delenv("CODEX_SESSIONS", raising=False)
    cfg = kitenv.load()
    assert cfg["CODEX_IDLE_MIN"] == "10"
    assert cfg["CODEX_SESSIONS"] == str(tmp_path / ".codex" / "sessions")
    (tmp_path / "f.env").write_text('CODEX_IDLE_MIN="3"\nCODEX_SESSIONS="$HOME/elsewhere"\n')
    monkeypatch.setenv("FACTORY_ENV", str(tmp_path / "f.env"))
    cfg = kitenv.load()
    assert cfg["CODEX_IDLE_MIN"] == "3" and cfg["CODEX_SESSIONS"] == str(tmp_path / "elsewhere")


def test_factory_env_example_documents_the_codex_keys():
    text = (Path(__file__).resolve().parents[1] / "factory.env.example").read_text()
    assert 'CODEX_IDLE_MIN="10"' in text and 'CODEX_SESSIONS="$HOME/.codex/sessions"' in text


def test_watchdog_main_builds_the_codex_watch_from_the_config(monkeypatch, tmp_path):
    w = watchdog.codex_watch_from_config({"CODEX_IDLE_MIN": "7", "CODEX_SESSIONS": str(tmp_path),
                                          "ASK_ME_STATE": str(tmp_path / "s")})
    assert w.idle_min == 7 and w.sessions == str(tmp_path)
    w = watchdog.codex_watch_from_config({"CODEX_IDLE_MIN": "nonsense", "CODEX_SESSIONS": str(tmp_path),
                                          "ASK_ME_STATE": str(tmp_path / "s")})
    assert w.idle_min == 10


def test_codex_processes_parses_lsof_output(monkeypatch):
    ps = "  10 /x/codex app-server --listen unix://\n  20 codex exec --cd /w -\n  30 /usr/bin/zsh\n"
    lsof = ("p10\nccodex\nfcwd\nn/\nf49\nn/h/.codex/sessions/2026/10/22/rollout-a.jsonl\n"
            "p20\nccodex\nfcwd\nn/w\nf3\nn/dev/null\n")

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = ps if cmd[0] == "ps" else lsof
        return R()

    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)
    procs = {p["pid"]: p for p in watchdog.codex_processes()}
    assert procs[10]["host"] is True and procs[10]["open"] == {"/h/.codex/sessions/2026/10/22/rollout-a.jsonl"}
    assert procs[20]["host"] is False and procs[20]["cwd"] == "/w" and procs[20]["open"] == set()


def test_codex_processes_reports_failure_as_none(monkeypatch):
    def fake_run(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)
    assert watchdog.codex_processes() is None


def test_the_watcher_reads_fixtures_without_prompt_text():
    # the fixtures hold record types, timestamps, ids and made-up last lines only
    for f in FIX.glob("*.jsonl"):
        for line in f.read_text().splitlines():
            rec = json.loads(line)
            p = rec.get("payload") or {}
            assert set(p) <= {"id", "timestamp", "cwd", "originator", "cli_version", "source", "thread_source",
                              "parent_thread_id", "type", "turn_id", "reason", "started_at", "completed_at",
                              "last_agent_message"}, (f.name, sorted(p))
            assert "/Users/" not in line or "/Users/alice/" in line


# --- found by the independent review (T18 review 1): each case is a counterexample it gave

def test_review1_no_burst_of_old_transitions_after_the_watchdog_was_down(tmp_path):
    w = World(tmp_path)
    w.add("t9-vscode-main.jsonl")
    w.add("t9-exec-stopped.jsonl")
    started(w, "2026-10-21T17:00:00Z")              # state saved before both runs
    w.now = epoch("2026-10-22T03:00:00Z")            # watchdog down 10 h, restarted
    w.make()
    assert w.at("2026-10-22T03:00:15Z") == []        # hours-old finish/idle/stop are recorded, not sent
    assert w.run("2026-10-22T03:00:30Z", "2026-10-22T04:00:00Z", step=60) == []


def test_review1_a_resumed_old_session_does_not_repeat_its_old_turn(tmp_path):
    w = World(tmp_path)
    r = w.add("parallel-app.jsonl")
    started(w, "2026-10-22T08:14:00Z")
    assert w.at("2026-10-22T08:30:00Z") == ["Codex idle 10 min after finishing: notes-app"]
    w.sent.clear()
    w.make()
    w.watch.told.clear()                             # transitions forgotten after days
    w.now = epoch("2026-10-26T09:00:00Z")
    with open(r.path, "a") as f:                     # `codex resume` appends context before the next prompt
        f.write(json.dumps({"timestamp": "2026-10-26T09:00:00Z", "type": "turn_context", "payload": {}}) + "\n")
    os.utime(r.path, (w.now, w.now))
    w.watch.check()
    w.now += 900
    w.watch.check()
    assert w.sent == []


def test_review1_more_secret_shapes_are_redacted():
    for secret in ("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                   "postgres://admin:Hunter2pass@db.example/x", "token=ghp_shorttoken123",
                   "password: S3cr3t!Passw0rd", "github_pat_11ABCDEFG0123456789_abcdefghijk"):
        out = first_line("see " + secret)
        assert "[redacted]" in out, (secret, out)
        for part in ("IOSFODNN7", "bPxRfiCY", "Hunter2pass", "shorttoken123", "S3cr3t", "0123456789_abc"):
            assert part not in out, (secret, out)


def test_review1_a_codex_exec_run_gets_finished_but_no_idle_message(tmp_path):
    w = World(tmp_path)
    r = w.add("t9-exec-stopped.jsonl")
    r.records.append({"timestamp": "2026-10-21T17:56:00.000Z", "type": "event_msg",
                      "payload": {"type": "task_complete", "turn_id": "01a15f9a-1180-7a21-9095-cb8cf96bfbd0",
                                  "last_agent_message": "Build done."}})
    started(w, "2026-10-21T17:54:50Z")
    assert w.run("2026-10-21T17:55:05Z", "2026-10-21T19:00:00Z") == ["Codex finished: t9-inbox-zero - Build done."]


def test_review1_turns_without_a_turn_id_are_each_told(tmp_path):
    w = World(tmp_path)
    r = w.add("parallel-app.jsonl")
    for rec in r.records:
        rec["payload"].pop("turn_id", None)
    r.records += [{"timestamp": "2026-10-22T08:14:00.000Z", "type": "event_msg", "payload": {"type": "task_started"}},
                  {"timestamp": "2026-10-22T08:15:00.000Z", "type": "event_msg",
                   "payload": {"type": "task_complete", "last_agent_message": "Second."}}]
    started(w, "2026-10-22T08:12:30Z")
    assert w.run("2026-10-22T08:13:10Z", "2026-10-22T08:15:10Z", step=60) == [
        "Codex finished: notes-app - Fixed the parser.", "Codex finished: notes-app - Second."]


def test_review1_a_failed_send_is_tried_again_up_to_three_times(tmp_path):
    w = World(tmp_path)
    w.add("parallel-app.jsonl")
    started(w, "2026-10-22T08:12:30Z")
    tries = []

    def flaky(text):
        tries.append(text)
        if len(tries) < 2:
            raise RuntimeError("Beeper down")
        w.sent.append(text)
    w.watch.send = flaky
    w.at("2026-10-22T08:13:10Z")
    w.at("2026-10-22T08:13:25Z")
    w.at("2026-10-22T08:13:40Z")
    assert w.sent == ["Codex finished: notes-app - Fixed the parser."] and len(tries) == 2

    def down(text):
        tries.append(text)
        raise RuntimeError("Beeper down")
    w.watch.send = down
    n = len(tries)
    w.run("2026-10-22T08:23:10Z", "2026-10-22T08:30:00Z")     # idle message: 3 tries, then given up
    assert len(tries) - n == 3


def test_review1_big_records_and_a_huge_first_line_are_read(tmp_path):
    w = World(tmp_path)
    r = w.add("parallel-app.jsonl")
    r.records.insert(2, {"timestamp": "2026-10-22T08:12:40.000Z", "type": "response_item",
                         "payload": {"type": "function_call_output", "output": "x" * (3 << 20)}})
    started(w, "2026-10-22T08:12:30Z")
    assert w.at("2026-10-22T08:13:10Z") == ["Codex finished: notes-app - Fixed the parser."]
    big = tmp_path / "sessions" / "2026" / "10" / "22" / "rollout-2026-10-22T08-00-00-big.jsonl"
    big.write_text(json.dumps({"type": "session_meta", "payload": {"id": "big", "cwd": "/w/big",
                                                                    "base_instructions": "y" * (5 << 20)}}) + "\n"
                   + json.dumps({"timestamp": "2026-10-22T08:13:00Z", "type": "event_msg",
                                 "payload": {"type": "task_complete", "turn_id": "b1", "last_agent_message": "ok"}})
                   + "\n")
    os.utime(big, (w.now, w.now))
    assert w.at("2026-10-22T08:13:25Z") == ["Codex finished: ? - ok"]
