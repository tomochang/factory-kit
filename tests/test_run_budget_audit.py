"""`run-budget audit --since D` (SPEC R10): the KGI and KPI checks over the cost log, the transcripts and the replies.

KGI measurement plan (dated). Install is his step at the Mac after both PRs merge. If he installs on 2026-11-02:
  - 2026-11-16 (install + 14 days): the foreman runs
        run-budget audit --since 2026-11-02
    on the live Mac (cost log ~/factory/.runs/runs.jsonl, transcripts
    ~/.claude/projects/-Users-<him>-factory/*/subagents/, replies <channel state>/replies.jsonl) and sends the table
    in a T33 report.
  - Target met when: 0 silent overruns, 0 finished T reports without a Cost line, and at least 20 runs.
  - KPIs read from the same output: K1 complete rows 100%, K2 notification match 100%, K3 partial work kept 100%,
    K4 wrong stops <= 1 in 20, K5 capped runs within +10% of the cap.
  If the install date differs, the measurement date moves with it (install + 14 days).
The tests below run the same code on a synthetic log so the checks themselves are proven before delivery.
"""
import json
import shutil
import subprocess
import sys

from rb_helpers import env, make_env, CODE, git_repo, FIX, assistant_row, rb, user_row


def row(aid, t="T50", **kw):
    r = {"agent_id": aid, "t": t, "description": "%s run" % t, "agent_type": "factory-builder",
         "model": "claude-haiku-5-5", "start": "2026-11-03T01:00:00Z", "end": "2026-11-03T01:10:00Z",
         "minutes": 10.0, "tool_uses": 10, "context_tokens": 50000,
         "tokens": {"input": 1, "output": 1, "cache_creation": 1, "cache_read": 1, "total": 4},
         "stop_reason": "completed", "worktree": "none", "last_commit": "none",
         "caps": rb.caps(rb.load_config({}), "factory-builder", "")}
    r.update(kw)
    return r


class Scene:
    def __init__(self, env):
        self.env = env
        self.rows = []
        self.replies = []

    def transcript(self, aid, ctx=50000, tools=10, minutes=10, agent_type="factory-builder", desc="T50 run"):
        rows = [user_row("2026-11-03T01:00:00Z")]
        rows.append(assistant_row("m-%s-1" % aid, "2026-11-03T01:00:10Z", ctx=ctx // 2, tools=tools))
        rows.append(assistant_row("m-%s-2" % aid, "2026-11-03T01:%02d:00Z" % minutes, ctx=ctx, tools=0))
        self.env.transcript(aid, rows=rows)
        self.env.meta(aid, agentType=agent_type, description=desc)

    def write(self):
        self.env.runs.mkdir(parents=True, exist_ok=True)
        (self.env.runs / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.rows))
        log = self.env.tmp / "replies.jsonl"
        log.write_text("".join(json.dumps({"t": rb.parse_ts(ts), "text": tx}) + "\n" for ts, tx in self.replies))
        return log

    def audit(self):
        log = self.write()
        return rb.audit(self.env.cfg(), "2026-11-02", self.env.proj, log)


def good_scene(env, n=20):
    s = Scene(env)
    for i in range(n):
        aid = "g%02d" % i
        s.transcript(aid)
        s.rows.append(row(aid, tokens={"input": 1, "output": 20, "cache_creation": 0,
                                       "cache_read": 25000 - 21 + 50000 - 21 + 2, "total": 75000},
                          context_tokens=50000, tool_uses=10))
    s.replies.append(("2026-11-03T02:00:00Z", "Nothing needed - T50 done\nCost: 20 runs, ctx max 50k"))
    return s


def test_clean_scene_meets_the_kgi(env):
    res = good_scene(env).audit()
    assert res["runs"] == 20
    assert res["silent_overruns"] == [] and res["missing_cost"] == []
    assert res["kgi_met"] is True
    assert res["k1_incomplete"] == [] and res["k5_overshoot"] == []


def test_too_few_runs_is_not_met(env):
    res = good_scene(env, n=5).audit()
    assert res["kgi_met"] is False and "fewer than 20" in rb.format_audit(res)


def test_silent_overrun_is_listed(env):
    s = good_scene(env)
    s.transcript("big", tools=230, desc="T50 big")  # over the 200 hard cap, and the row says completed
    s.rows.append(row("big", tool_uses=230))
    s.transcript("norow", ctx=450000, desc="T50 lost")  # over the context cap and no row at all
    res = s.audit()
    assert {x["agent_id"] for x in res["silent_overruns"]} == {"big", "norow"}
    assert res["kgi_met"] is False


def test_capped_run_with_stop_row_is_not_silent_but_overshoot_is_checked(env):
    s = good_scene(env)
    s.transcript("cap1", tools=205, desc="T50 cap")
    s.rows.append(row("cap1", tool_uses=205, stop_reason="cap:tools"))
    s.transcript("cap2", tools=260, desc="T50 cap")
    s.rows.append(row("cap2", tool_uses=260, stop_reason="killed:cap:tools"))
    res = s.audit()
    assert res["silent_overruns"] == []
    assert [x["agent_id"] for x in res["k5_overshoot"]] == ["cap2"]


def test_missing_cost_line(env):
    s = good_scene(env)
    s.rows.append(row("t51", t="T51", end="2026-11-04T01:00:00Z"))
    s.transcript("t51", desc="T51 run")
    s.replies.append(("2026-11-04T02:00:00Z", "Nothing needed - T51 done, tests pass"))
    res = s.audit()
    assert res["missing_cost"] == ["T51"]


def test_k1_incomplete_and_token_mismatch(env):
    s = good_scene(env)
    s.rows.append(row("i1", model="", context_tokens="unknown"))
    s.transcript("i1")
    res = s.audit()
    assert [x["agent_id"] for x in res["k1_incomplete"]] == ["i1"]


def test_k2_against_real_notifications(env):
    """The two real runs: logged context and tool uses equal the notification numbers."""
    s = Scene(env)
    for aid, ctx, tools, cum in (("a0ac34e06a070ed16", 80350, 19, 258260), ("ac2e569581df4044a", 98977, 35, 369318)):
        env.transcript(aid, FIX / "transcripts" / ("agent-%s.jsonl" % aid))
        s.rows.append(row(aid, t="T33", context_tokens=ctx, tool_uses=tools, start="2026-11-03T01:00:00Z",
                          tokens={"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0, "total": cum}))
    s.rows.append(row("wrong", t="T33", context_tokens=1, tool_uses=1))
    shutil.copyfile(FIX / "transcripts" / "parent-notifications.jsonl", env.main)
    (env.proj / "s-0001" / "subagents" / "agent-wrong.jsonl").write_text("")
    with env.main.open("a") as f:
        f.write(json.dumps({"type": "queue-operation", "content": "<task-notification>\n<task-id>wrong</task-id>\n"
                            "<status>completed</status>\n<summary>x</summary>\n<usage><subagent_tokens>5"
                            "</subagent_tokens><tool_uses>2</tool_uses><duration_ms>1</duration_ms></usage>\n"
                            "</task-notification>"}) + "\n")
    res = s.audit()
    assert res["k2_checked"] == 3
    assert [x["agent_id"] for x in res["k2_mismatch"]] == ["wrong"]


def test_k3_partial_work_kept(tmp_path, env):
    s = good_scene(env)
    wt = git_repo(tmp_path / "wt")
    head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    s.rows.append(row("st1", t="T52", stop_reason="stall:repeat", worktree=str(wt), last_commit=head,
                      start="2000-01-01T00:00:00Z"))
    s.rows.append(row("st2", t="T52", stop_reason="cap:minutes", worktree=str(tmp_path / "gone"),
                      last_commit="none"))
    s.transcript("st1", desc="T52 a")
    s.transcript("st2", desc="T52 b")
    s.replies.append(("2026-11-05T00:00:00Z", "Nothing needed - T52 stopped\nCost: 2 runs, stops: stall:repeat 1, "
                                              "cap:minutes 1"))
    res = s.audit()
    assert [x["agent_id"] for x in res["k3_lost"]] == ["st2"]


def test_k4_wrong_stops(env):
    s = good_scene(env)
    s.rows.append(row("w1", stop_reason="stall:repeat", end="2026-11-03T02:00:00Z"))
    s.rows.append(row("w1", stop_reason="completed", end="2026-11-03T03:00:00Z"))
    s.transcript("w1")
    res = s.audit()
    assert res["k4_wrong_stops"] == ["w1"]


def test_cli_audit_exit_code(env):
    s = good_scene(env)
    log = s.write()
    cmd = [sys.executable, str(CODE), "audit", "--since", "2026-11-02", "--projects",
           str(env.proj), "--replies", str(log)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KGI met" in r.stdout
