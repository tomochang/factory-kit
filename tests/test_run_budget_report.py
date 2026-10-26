"""`run-budget report T{n}` and the reply guard's Cost-line rule (SPEC R7; KGI; Done-when 5)."""
import json
import shutil
import subprocess
import sys

from rb_helpers import env, make_env, CODE, CLI, start_from_agent_call, FIX, decision, rb, reason


def two_real_runs(env):
    for aid, desc in (("a0ac34e06a070ed16", "T33 Search B"), ("ac2e569581df4044a", "T33 Search A")):
        env.transcript(aid, FIX / "transcripts" / ("agent-%s.jsonl" % aid))
        start_from_agent_call(env, aid, desc)
        env.now = rb.parse_ts("2026-10-26T01:31:00Z")
        env.bash(aid, "echo hi")
        env.stop(aid, agent_type="general-purpose")


def test_report_line_two_real_runs(env):
    two_real_runs(env)
    line = rb.report_line(env.cfg(), "T33")
    assert line.startswith("Cost: 2 runs, ctx max 98k, total 627k tok, 54 tools, ")
    assert line.endswith(" min, stops: completed")
    assert len(line) < 120


def test_report_mixed_stops_and_label(env):
    rows = [
        {"agent_id": "x1", "t": "T5", "context_tokens": 120000, "tokens": {"total": 400000}, "tool_uses": 201,
         "minutes": 30.2, "stop_reason": "cap:tools", "end": "2026-10-26T01:00:00Z"},
        {"agent_id": "x2", "t": "T5", "context_tokens": "unknown", "tokens": {"total": "unknown"}, "tool_uses": 3,
         "minutes": 1.0, "stop_reason": "completed", "end": "2026-10-26T01:00:00Z"},
    ]
    env.runs.mkdir(parents=True, exist_ok=True)
    (env.runs / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    line = rb.report_line(env.cfg(), "T5", label=True)
    assert line == "Cost T5: 2 runs, ctx max 120k, total 400k+ tok (1 unknown), 204 tools, 31 min, " \
                   "stops: cap:tools 1, completed 1"


def test_report_no_runs(env):
    assert rb.report_line(env.cfg(), "T77") == "Cost: no runs logged for T77"


def test_cli_report(env):
    two_real_runs(env)
    r = subprocess.run([sys.executable, str(CODE), "report", "T33"], capture_output=True,
                       text=True, timeout=10)
    assert r.returncode == 0 and r.stdout.startswith("Cost: 2 runs")
    r = subprocess.run(["bash", str(CLI), "report", "33"], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and r.stdout.startswith("Cost: 2 runs")


FINAL = "Nothing needed - T33 done\nBuilt run_budget.py, 80 tests pass.\nREA: Python not covered."


def test_reply_without_cost_line_is_denied(env):
    two_real_runs(env)
    problems = rb.reply_problems(FINAL, env.cfg())
    assert len(problems) == 1
    assert "Cost:" in problems[0] and "Cost: 2 runs" in problems[0]  # the hint carries the line to paste


def test_reply_with_cost_line_passes_and_marks_reported(env):
    two_real_runs(env)
    text = FINAL + "\n" + rb.report_line(env.cfg(), "T33")
    assert rb.reply_problems(text, env.cfg()) == []
    # the foreman's PostToolUse on the sent reply marks T33 as reported
    env.call(env.event("PostToolUse", None, tool_name="mcp__whatsapp__reply", tool_input={"text": text},
                       tool_response={"ok": True}))
    assert rb.reply_problems("Nothing needed - T33 merged later, no new runs", env.cfg()) == []


def test_new_run_after_report_needs_a_new_cost_line(env):
    two_real_runs(env)
    env.call(env.event("PostToolUse", None, tool_name="mcp__whatsapp__reply",
                       tool_input={"text": FINAL + "\nCost: 2 runs"}, tool_response={}))
    env.now += 600
    env.meta("z1", description="T33 fix")
    env.bash("z1", "echo 1")
    env.stop("z1")
    assert rb.reply_problems("Nothing needed - T33 fixed", env.cfg())


def test_receipts_and_other_tasks_pass(env):
    two_real_runs(env)
    assert rb.reply_problems("Got it - T33 started, building in a worktree", env.cfg()) == []
    assert rb.reply_problems("Nothing needed - T34 waiting go", env.cfg()) == []
    assert rb.reply_problems("You: reply F: go T35", env.cfg()) == []


def test_two_pending_tasks_need_labelled_lines(env):
    two_real_runs(env)
    env.meta("z2", description="T40 build")
    env.bash("z2", "echo 1")
    env.stop("z2")
    text = "Nothing needed - T33 and T40 done\nCost: 2 runs"
    assert rb.reply_problems(text, env.cfg())
    text = "Nothing needed - T33 and T40 done\n%s\n%s" % (rb.report_line(env.cfg(), "T33", label=True),
                                                       rb.report_line(env.cfg(), "T40", label=True))
    assert rb.reply_problems(text, env.cfg()) == []


def test_reply_hook_mode_denies(env):
    """Registered as its own PreToolUse hook on the reply tool, run_budget.py denies the reply itself."""
    two_real_runs(env)
    out = env.call(env.event("PreToolUse", None, tool_name="mcp__whatsapp__reply", tool_input={"text": FINAL}))
    assert decision(out) == "deny" and "Cost:" in reason(out)
