"""Cost log (SPEC R6, R12; KPI K1, K3; Done-when 4): one complete row per run in runs.jsonl.

Covers completed, capped, stalled, killed and hook-error runs, the late-flushed final transcript row, runs that end
without SubagentStop (maxTurns, seen in the probe), helper subagents (no row) and unknown tokens (never zero).
"""
import json
import shutil
import subprocess
import sys
import threading
import time

from rb_helpers import env, make_env, CODE, start_from_agent_call, git_repo, FIX, assistant_row, decision, rb, user_row

FIELDS = ("agent_id", "t", "description", "agent_type", "model", "start", "end", "minutes", "tool_uses",
          "context_tokens", "tokens", "stop_reason", "worktree", "last_commit")


def complete(row):
    return [k for k in FIELDS if row.get(k) in (None, "")]




def test_completed_row_from_real_transcript(tmp_path, env):
    aid = "a0ac34e06a070ed16"
    env.transcript(aid, FIX / "transcripts" / ("agent-%s.jsonl" % aid))
    shutil.copyfile(FIX / "transcripts" / ("agent-%s.meta.json" % aid), env.subdir / ("agent-%s.meta.json" % aid))
    wt = git_repo(tmp_path / "wt")
    start_from_agent_call(env, aid, "T33 Search B: practitioner write-ups")
    env.now = rb.parse_ts("2026-10-26T01:30:00Z")
    env.bash(aid, "git -C %s commit --allow-empty -m 'T33: notes'" % wt)
    env.stop(aid, agent_type="general-purpose")
    rows = env.rows()
    assert len(rows) == 1
    r = rows[0]
    assert complete(r) == []
    assert r["t"] == "T33" and r["agent_type"] == "general-purpose" and r["model"] == "claude-haiku-5-5"
    assert r["context_tokens"] == 80350 and r["tool_uses"] == 19
    assert r["tokens"] == {"input": 10, "output": 20139, "cache_creation": 62141, "cache_read": 175970,
                           "total": 258260}
    assert r["stop_reason"] == "completed"
    assert r["worktree"] == str(wt.resolve())
    head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert r["last_commit"] == head
    assert r["minutes"] >= 0 and r["start"] < r["end"]


def test_capped_row(make_env):
    env = make_env(RB_TOOLS_WARN=1, RB_TOOLS_HARD=2)
    for c in ("echo 1", "echo 2", "echo 3"):
        env.bash("k1", c, stdout=c)
    env.stop("k1")
    r = env.rows()[-1]
    assert r["stop_reason"] == "cap:tools" and r["caps"]["tools_hard"] == 2
    assert r["worktree"] == "none" and r["last_commit"] == "none"  # no worktree seen: said, not empty


def test_killed_row_written_once(make_env):
    env = make_env(RB_TOOLS_WARN=1, RB_TOOLS_HARD=1, RB_KILL_AFTER=2)
    for c in ("echo 1", "echo 2", "echo 3", "echo 4", "echo 5"):
        env.pre("k2", "Bash", {"command": c})
    env.call(env.event("Stop", None))  # the foreman's sweep must not add a second row
    rows = env.rows()
    assert [r["stop_reason"] for r in rows] == ["killed:cap:tools"]


def test_stalled_row(env):
    for _ in range(6):
        env.bash("k3", "echo x", stdout="x")
    env.stop("k3")
    assert env.rows()[-1]["stop_reason"] == "stall:repeat"


def test_hook_error_run_still_gets_a_complete_row(env, monkeypatch):
    env.bash("k4", "echo 1")
    real = rb.on_post

    def boom(*a, **k):
        raise RuntimeError("simulated")
    monkeypatch.setattr(rb, "on_post", boom)
    out = rb.run_hook(json.dumps(env.event("PostToolUse", "k4", tool_name="Bash", tool_input={"command": "x"},
                                           tool_response={"stdout": ""})), env.cfg())
    assert out == ""  # fail-open: nothing printed, the call is not affected
    monkeypatch.setattr(rb, "on_post", real)
    env.bash("k4", "echo 2")
    env.stop("k4")
    r = env.rows()[-1]
    assert r["hook_errors"] == 1 and r["stop_reason"] == "completed"
    assert "hook-error" in (env.runs / "hook-errors.log").read_text()


def test_hook_error_in_block_stage_keeps_the_block(make_env, monkeypatch):
    env = make_env(RB_TOOLS_WARN=1, RB_TOOLS_HARD=1)
    env.bash("k5", "echo 1")
    assert decision(env.pre("k5", "Bash", {"command": "echo 2"})) == "deny"

    def boom(*a, **k):
        raise RuntimeError("simulated")
    monkeypatch.setattr(rb, "on_pre", boom)
    out = rb.run_hook(json.dumps(env.event("PreToolUse", "k5", tool_name="Bash", tool_input={"command": "ls"})),
                      env.cfg())
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    # and a broken hook outside the block stage lets the call through
    out = rb.run_hook(json.dumps(env.event("PreToolUse", "k6", tool_name="Bash", tool_input={"command": "ls"})),
                      env.cfg())
    assert out == ""


def test_bad_stdin_fails_open(env):
    assert rb.run_hook("not json", env.cfg()) == ""


def test_late_flushed_final_row_is_picked_up(make_env):
    env = make_env(RB_STOP_POLL_S=3)
    p = env.transcript("k7", rows=[user_row("2026-10-26T01:00:00Z"),
                                   assistant_row("m1", "2026-10-26T01:00:01Z", ctx=1000, tools=1)])
    env.now = rb.parse_ts("2026-10-26T01:00:02Z")
    env.bash("k7", "echo 1")

    def late():
        time.sleep(0.6)
        with p.open("a") as f:
            f.write(json.dumps(assistant_row("m2", "2026-10-26T01:00:03Z", ctx=1500, tools=0)) + "\n")
    th = threading.Thread(target=late)
    th.start()
    env.stop("k7")
    th.join()
    r = env.rows()[-1]
    assert r["context_tokens"] == 1500 and r["tokens"]["total"] == 2500


def test_max_turns_run_without_subagent_stop_is_swept(env):
    """Probe 5: a maxTurns stop fires no SubagentStop; the foreman's next Stop writes the row from the notification."""
    aid = "a6f4678cb6fd2de44"
    env.transcript(aid, FIX / "probe" / ("agent-%s.jsonl" % aid))
    env.meta(aid, agentType="probe-turns", description="T99 probe turns", finished=True)
    env.now = rb.parse_ts("2026-10-26T03:28:00Z")
    env.bash(aid, "echo t1", stdout="t1")
    env.bash(aid, "echo t2", stdout="t2")
    shutil.copyfile(FIX / "probe" / "main-session.jsonl", env.main)
    env.now += 60
    env.call(env.event("Stop", None))
    r = env.rows()[-1]
    assert r["agent_id"] == aid and r["stop_reason"] == "killed:max-turns" and r["t"] == "T99"
    assert r["context_tokens"] == 33764 and r["tool_uses"] == 2
    assert r["notification"] == {"subagent_tokens": 33764, "tool_uses": 2}
    env.now += 60
    env.call(env.event("Stop", None))
    assert len(env.rows()) == 1


def test_helper_subagent_gets_no_row(env):
    env.stop("helper1", agent_type="")
    assert env.rows() == []


def test_unknown_tokens_are_not_zero(env):
    env.bash("k8", "echo 1")
    env.stop("k8")
    r = env.rows()[-1]
    assert r["context_tokens"] == "unknown" and r["tokens"]["total"] == "unknown"
    assert r["tool_uses"] == 1  # from the hook's own count when the transcript is missing


def test_t_number_from_meta_or_prompt(env):
    env.meta("k9", description="T41 research")
    env.bash("k9", "echo 1")
    env.stop("k9")
    assert env.rows()[-1]["t"] == "T41"
    env.transcript("k10", rows=[user_row("2026-10-26T01:00:00Z", "You are the T52 build agent."),
                                assistant_row("m1", "2026-10-26T01:00:01Z")])
    env.now = rb.parse_ts("2026-10-26T01:00:02Z")
    env.bash("k10", "echo 1")
    env.stop("k10")
    assert env.rows()[-1]["t"] == "T52"


def test_resumed_run_last_row_wins(env):
    env.meta("k11", description="T60 x")
    env.bash("k11", "echo 1")
    env.stop("k11")
    env.bash("k11", "echo 2")
    env.stop("k11")
    rows = rb.read_rows(env.cfg())
    assert len([r for r in rows if r["agent_id"] == "k11"]) == 1
    assert [r for r in rows if r["agent_id"] == "k11"][0]["tool_uses"] == 2


def test_cli_hook_mode_subprocess(env):
    ev = env.event("PreToolUse", "s1", tool_name="Bash", tool_input={"command": "echo 1"})
    r = subprocess.run([sys.executable, str(CODE)], input=json.dumps(ev), capture_output=True,
                       text=True, timeout=10)
    assert r.returncode == 0 and r.stdout.strip() == ""
    assert (env.runs / "state" / "s1.json").exists()
