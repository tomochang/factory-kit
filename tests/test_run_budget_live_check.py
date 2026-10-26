"""The M4 live check, replayed (SPEC Done-when 6).

Recorded 2026-10-26 in a scratch interactive session (/private/tmp/t33-live, Claude Code 2.1.310, haiku) with
run_budget.py registered as in settings-hooks.json: a factory-builder subagent told to run `echo x` ten times was
denied at the 3rd identical call, committed its work in its worktree (7c3bf2e), handed back "STOPPED: ...", and its
row says stall:repeat; `run-budget report T99` printed
"Cost: 1 run, ctx max 36k, total 206k tok, 6 tools, 0 min, stops: stall:repeat". Its notification said
subagent_tokens 36252, tool_uses 6 (K2).
Earlier attempts in the same scratch session found two gaps, now covered by tests in test_run_budget_stall.py and
test_run_budget_ladder.py: the run dodged the rule by appending "# call 3 of 10" (comments are now ignored), and a multi-line
commit message was not seen as a save-set call (operators are now parsed with quotes).
Fixtures: the hook events (home path rewritten, committer identity replaced) and the subagent transcript.
"""
import json
import shutil

from rb_helpers import env, make_env, FIX, decision, rb

LIVE = FIX / "live"
AID = "a243fa6295f13a836"


def test_replay_live_check(env):
    events = [json.loads(l) for l in (LIVE / "events-live-check.jsonl").read_text().splitlines()]
    shutil.copyfile(LIVE / ("agent-%s.jsonl" % AID), env.subdir / ("agent-%s.jsonl" % AID))
    outs = []
    for ev in events:
        ev = dict(ev, transcript_path=str(env.main))
        ev.pop("agent_transcript_path", None)
        t = ev.pop("_t")
        out = rb.handle(ev, env.cfg(), now=t)
        if ev.get("agent_id") == AID and ev["hook_event_name"] == "PreToolUse":
            outs.append((ev["tool_name"], (ev.get("tool_input") or {}).get("command"), decision(out)))
    bash = [o for o in outs if o[0] == "Bash"]
    assert bash[0][2] is None and bash[1][2] is None  # echo x, echo x
    assert bash[2][1] == "echo x" and bash[2][2] == "deny"  # the 3rd identical call
    assert bash[3][1].startswith("git -C ") and bash[3][2] is None  # its commit (save set) goes through
    assert outs[-1][0] == "SubagentHandback" and outs[-1][2] is None
    rows = env.rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["stop_reason"] == "stall:repeat" and r["t"] == "T99" and r["agent_type"] == "factory-builder"
    assert r["context_tokens"] == 36252 and r["tool_uses"] == 6
    notes = rb.parse_notifications(LIVE / "main-session.jsonl")
    assert notes[AID]["subagent_tokens"] == 36252 and notes[AID]["tool_uses"] == 6
    assert rb.report_line(env.cfg(), "T99").endswith("6 tools, 0 min, stops: stall:repeat")
