"""Agent types (SPEC R9), hook settings (R1, R6, R12), config (R8) and the probe fixtures (REFERENCE.md, probe 1-5)."""
import json
import re

from rb_helpers import env, make_env, AGENTS, CONF, SETTINGS, FIX, rb


def frontmatter(path):
    text = path.read_text()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, path
    return dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line), text[m.end():]


def test_agent_types_have_max_turns_backstop():
    for name in ("factory-builder", "factory-researcher"):
        fm, body = frontmatter(AGENTS / ("%s.md" % name))
        assert fm["name"] == name
        assert fm["maxTurns"] == "200"
        assert fm.get("description")
        assert "STOPPED:" in body and "PARTIAL.md" in body  # how to hand back when run-budget blocks


def rb_hooks(settings, event):
    """[(matcher, hook)] for every run_budget.py hook registered on event."""
    return [(e.get("matcher"), h) for e in settings.get(event, []) for h in e["hooks"]
            if "run_budget.py" in h.get("command", "")]


def test_settings_register_every_event():
    s = json.loads(SETTINGS.read_text())["hooks"]
    for ev in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        ((matcher, h),) = rb_hooks(s, ev)
        assert matcher == "*"
        assert h["timeout"] == 5 and h.get("onFailure", "continue") == "continue"
    for ev in ("SubagentStop", "Stop"):
        assert len(rb_hooks(s, ev)) == 1
    assert rb_hooks(s, "SubagentStop")[0][1]["timeout"] >= 10  # polls the transcript up to 5 s


def test_conf_file_has_his_defaults():
    cfg = rb.load_config({"RUN_BUDGET_CONF": str(CONF), "HOME": "/Users/you"})
    c = rb.caps(cfg, "factory-builder", "")
    assert (c["ctx_warn"], c["ctx_hard"], c["tools_warn"], c["tools_hard"], c["min_warn"], c["min_hard"]) == \
        (300000, 400000, 150, 200, 45, 60)
    assert cfg["RUN_BUDGET_DIR"] == "/Users/you/factory/.runs"


def events(name):
    return [json.loads(l) for l in (FIX / "probe" / ("events-%s.jsonl" % name)).read_text().splitlines()]


def test_probe_hooks_fire_in_background_subagents_with_agent_id():
    ev = events("deny")
    sub = [e for e in ev if e["hook_event_name"] == "PreToolUse" and e.get("tool_name") == "Bash"]
    assert sub and all(e.get("agent_id") == "a7b5cd41c23259814" and e.get("agent_type") == "probe-worker" for e in sub)
    foreman = [e for e in ev if e.get("tool_name") == "Agent"]
    assert foreman and all("agent_id" not in e for e in foreman)
    # transcript_path is the foreman's; the subagent transcript is derived from it
    assert all(not e["transcript_path"].endswith("agent-a7b5cd41c23259814.jsonl") for e in sub)
    stop = [e for e in ev if e["hook_event_name"] == "SubagentStop" and e.get("agent_type")]
    assert stop[0]["agent_transcript_path"].endswith("/subagents/agent-a7b5cd41c23259814.jsonl")
    assert stop[0]["agent_transcript_path"].startswith(sub[0]["transcript_path"][:-len(".jsonl")])


def test_probe_deny_lets_the_subagent_continue():
    ev = events("deny")
    cmds = [(e["hook_event_name"], e["tool_input"].get("command")) for e in ev if e.get("tool_name") == "Bash"]
    assert ("PreToolUse", "echo PROBE_DENY") in cmds and ("PostToolUse", "echo PROBE_DENY") not in cmds
    assert ("PostToolUse", "echo step3") in cmds
    assert any(e.get("tool_name") == "SubagentHandback" for e in ev)


def test_probe_continue_false_stops_only_the_subagent_after_the_batch():
    ev = events("continue-false")
    cmds = [(e["hook_event_name"], e["tool_input"].get("command")) for e in ev if e.get("tool_name") == "Bash"]
    assert ("PostToolUse", "echo PROBE_STOP") in cmds  # the answered call still ran
    assert ("PostToolUse", "echo after") in cmds  # and the other call of the same response
    aid = next(e["agent_id"] for e in ev if e.get("tool_name") == "Bash")
    assert not any(e["hook_event_name"] == "SubagentStop" and e.get("agent_id") == aid for e in ev)
    assert [e for e in ev if e["hook_event_name"] == "Stop"][-1].get("last_assistant_message")  # foreman went on


def test_probe_max_turns_honoured_without_subagent_stop():
    ev = events("max-turns")
    bash = [e for e in ev if e["hook_event_name"] == "PreToolUse" and e.get("tool_name") == "Bash"]
    assert [e["tool_input"]["command"] for e in bash] == ["echo t1", "echo t2"]
    aid = bash[0]["agent_id"]
    assert not any(e["hook_event_name"] == "SubagentStop" and e.get("agent_id") == aid for e in ev)
    notes = rb.parse_notifications(FIX / "probe" / "main-session.jsonl")
    assert "2-turn limit" in notes[aid]["summary"]


def test_probe_workflow_subagents_also_carry_agent_id():
    ev = events("workflow")
    sub = [e for e in ev if e["hook_event_name"] == "PreToolUse" and e.get("tool_name") == "Bash"]
    assert sub and all(e.get("agent_type") == "workflow-subagent" and e.get("agent_id") for e in sub)
    stop = [e for e in ev if e["hook_event_name"] == "SubagentStop" and e.get("agent_type") == "workflow-subagent"]
    assert "/subagents/workflows/" in stop[0]["agent_transcript_path"]
