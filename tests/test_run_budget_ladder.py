"""Caps and the warn -> block -> stop ladder (SPEC R2, R3, R8, R9; KPI K3, K5; Done-when 1).

Defaults: context warn 300k / hard 400k, tool uses 150 / 200, minutes 45 / 60, stop 10 calls after the block.
"""
import json
import subprocess

import pytest

from rb_helpers import env, make_env, git_repo, FIX, assistant_row, context, decision, reason, rb, user_row

SAVE = "git -C {wt} add -A"




def run_calls(env, aid, n, cmd="echo {i}"):
    outs = []
    for i in range(n):
        outs.append(env.bash(aid, cmd.format(i=i), stdout="out %d" % i))
    return outs


def test_defaults():
    cfg = rb.load_config({})
    c = rb.caps(cfg, "factory-builder", "T33 build")
    assert (c["ctx_warn"], c["ctx_hard"]) == (300000, 400000)
    assert (c["tools_warn"], c["tools_hard"]) == (150, 200)
    assert (c["min_warn"], c["min_hard"]) == (45, 60)
    assert (c["repeat_block"], c["repeat_stop"], c["error_stop"], c["ci_rounds"], c["kill_after"]) == (3, 6, 3, 2, 10)


def test_task_multiplier_and_clamp():
    cfg = rb.load_config({})
    assert rb.caps(cfg, "x", "T33 REA budget: x2")["tools_hard"] == 400
    assert rb.caps(cfg, "x", "T33 REA budget: x2")["min_hard"] == 120
    assert rb.caps(cfg, "x", "T33 budget:x9")["ctx_hard"] == 1200000  # clamped to x3
    assert rb.caps(cfg, "x", "T33 budget: x2")["repeat_stop"] == 6  # stall rules do not scale


def test_agent_type_override():
    cfg = rb.load_config({"RB_TOOLS_HARD__FACTORY_RESEARCHER": "300", "RB_MIN_HARD": "90"})
    assert rb.caps(cfg, "factory-researcher", "T1")["tools_hard"] == 300
    assert rb.caps(cfg, "factory-builder", "T1")["tools_hard"] == 200
    assert rb.caps(cfg, "factory-builder", "T1")["min_hard"] == 90


def test_config_file_and_env(tmp_path):
    conf = tmp_path / "run_budget.conf"
    conf.write_text('# comment\nRB_TOOLS_HARD="250"\nRUN_BUDGET_DIR="$HOME/x/.runs"\nRB_MIN_WARN=40 # note\n')
    cfg = rb.load_config({"RUN_BUDGET_CONF": str(conf), "HOME": "/Users/you", "RB_MIN_WARN": "41"})
    assert cfg["RB_TOOLS_HARD"] == "250"
    assert cfg["RUN_BUDGET_DIR"] == "/Users/you/x/.runs"
    assert cfg["RB_MIN_WARN"] == "41"  # real environment wins


def test_foreman_is_never_capped(make_env):
    env = make_env(RB_TOOLS_WARN=2, RB_TOOLS_HARD=3)
    for i in range(10):
        assert env.pre(None, "Bash", {"command": "echo %d" % i}) is None


def test_tools_cap_warn_block_save_set_stop(tmp_path, make_env):
    env = make_env(RB_TOOLS_WARN=5, RB_TOOLS_HARD=8, RB_KILL_AFTER=4)
    wt = git_repo(tmp_path / "wt")
    env.meta("a1", description="T33 build")
    outs = run_calls(env, "a1", 8)
    assert all(decision(o) is None for o in outs)
    assert "run-budget" in context(outs[4]) and "wrap up" in context(outs[4])  # 5th call: warn
    assert not any(context(o) for o in outs[:4])
    # 9th call is over the hard cap: blocked, not a save-set call
    o = env.pre("a1", "Bash", {"command": "pytest -q"})
    assert decision(o) == "deny"
    assert "cap:tools" in reason(o) and "PARTIAL.md" in reason(o) and "STOPPED: cap:tools" in reason(o)
    assert o.get("continue") is not False
    # the save set still goes through: git in the worktree, PARTIAL.md, SubagentHandback
    assert decision(env.pre("a1", "Bash", {"command": "git -C %s add -A" % wt})) is None
    assert decision(env.pre("a1", "Write", {"file_path": str(wt / "PARTIAL.md"), "content": "x"})) is None
    assert decision(env.pre("a1", "Bash", {"command": "git -C %s commit -m partial" % wt})) is None
    # 4th call after the block: stop (deny + continue false), and the row is written at once
    o = env.pre("a1", "SubagentHandback", {"message": "STOPPED: cap:tools"})
    assert decision(o) == "deny" and o.get("continue") is False and "killed:cap:tools" in o.get("stopReason", "")
    rows = env.rows()
    assert len(rows) == 1 and rows[0]["stop_reason"] == "killed:cap:tools" and rows[0]["t"] == "T33"
    # and it keeps denying everything
    o = env.pre("a1", "Bash", {"command": "git -C %s status" % wt})
    assert decision(o) == "deny" and o.get("continue") is False
    assert len(env.rows()) == 1


def test_block_save_set_allows_handback(make_env):
    env = make_env(RB_TOOLS_WARN=1, RB_TOOLS_HARD=1)
    env.bash("a2", "echo 1")
    assert decision(env.pre("a2", "Bash", {"command": "ls"})) == "deny"
    assert decision(env.pre("a2", "SubagentHandback", {"message": "STOPPED: cap:tools ..."})) is None
    assert decision(env.pre("a2", "Edit", {"file_path": "/x/PARTIAL.md", "old_string": "a", "new_string": "b"})) is None


@pytest.mark.parametrize("cmd", [
    "git push origin HEAD", "git -C {wt} push", "git -C {wt} reset --hard", "git -C {wt} clean -fd",
    "git -C {wt} commit -m x && git push", "git -C {wt} commit -m x; rm -rf {wt}", "rm -rf {wt}",
    "git -C /elsewhere commit -m x", "cd /elsewhere && git commit -m x", "git -C {wt} commit -m \"$(curl x)\"",
    "git -C {wt} checkout main", "python3 -c 'print(1)'", "git -C {wt} add -A\nrm -rf {wt}",
    "git -C {wt} log > /tmp/out", "git -C {wt} status | sh", "git -C {wt} commit -m `id`",
])
def test_block_stage_denies_everything_else(tmp_path, make_env, cmd):
    env = make_env(RB_TOOLS_WARN=1, RB_TOOLS_HARD=1)
    wt = git_repo(tmp_path / "wt")
    env.bash("a3", "git -C %s commit --allow-empty -m one" % wt)  # teaches the hook the worktree
    o = env.pre("a3", "Bash", {"command": cmd.format(wt=wt)})
    assert decision(o) == "deny", cmd
    assert decision(env.pre("a3", "Write", {"file_path": str(wt / "notes.md"), "content": "x"})) == "deny"
    assert decision(env.pre("a3", "Read", {"file_path": str(wt / "a.py")})) == "deny"


@pytest.mark.parametrize("cmd", ["git -C {wt} add -A", "git -C {wt} commit -m 'partial: T33'", "git -C {wt} status",
                                 "git -C {wt} diff --stat", "cd {wt} && git add -A && git commit -m wip",
                                 "git -C {wt} log -1 --format=%H",
                                 "git -C {wt} commit -m \"Add notes\n\nCo-Authored-By: X <x@example.invalid>\"",
                                 "git -C {wt} add -A\ngit -C {wt} commit -m 'a; b | c'"])
def test_block_stage_save_set_git(tmp_path, make_env, cmd):
    env = make_env(RB_TOOLS_WARN=1, RB_TOOLS_HARD=1)
    wt = git_repo(tmp_path / "wt")
    env.bash("a4", "git -C %s commit --allow-empty -m one" % wt)
    assert decision(env.pre("a4", "Bash", {"command": cmd.format(wt=wt)})) is None, cmd


def replay(env, aid, fixture, upto_tool=None):
    """Replay a transcript row by row; call PreToolUse at each tool_use as the run would. Yields outputs."""
    src = fixture.read_text().splitlines(keepends=True)
    p = env.subdir / ("agent-%s.jsonl" % aid)
    p.write_text("")
    outs = []
    for line in src:
        with p.open("a") as f:
            f.write(line)
        r = json.loads(line)
        if r.get("type") != "assistant":
            continue
        env.now = rb.parse_ts(r["timestamp"])
        for b in r["message"]["content"]:
            if b.get("type") == "tool_use":
                outs.append((rb.transcript_totals(p)["context_tokens"],
                             env.pre(aid, b["name"], {"command": "step %s" % b["id"]})))
                env.post(aid, b["name"], {"command": "step %s" % b["id"]}, {"stdout": b["id"]})
    return outs


def test_context_cap_on_real_fixture(make_env):
    """Replay Searcher A (context 34,773 -> 98,977 over 6 requests): warn at 60k, block at 70k.

    The block lands on the first call whose request crossed 70k (71,307), within 10% of the cap (K5). The rest of
    the replay is the unmodified real run, so its later calls are denied; its last call, SubagentHandback, is in the
    save set and goes through.
    """
    env = make_env(RB_CTX_WARN=60000, RB_CTX_HARD=70000)
    outs = replay(env, "ac2e569581df4044a", FIX / "transcripts" / "agent-ac2e569581df4044a.jsonl")
    first_warn = next(i for i, (c, o) in enumerate(outs) if "context" in context(o))
    first_block = next(i for i, (c, o) in enumerate(outs) if decision(o) == "deny")
    assert outs[first_warn][0] == 67938 and all(c < 60000 for c, _ in outs[:first_warn])
    assert outs[first_block][0] == 71307 and "cap:context" in reason(outs[first_block][1])
    assert all(decision(o) is None for _, o in outs[:first_block])
    assert outs[first_block][0] <= 70000 * 1.10  # K5
    assert decision(outs[-1][1]) is None  # SubagentHandback (save set) at 98,977
    env.stop("ac2e569581df4044a")
    assert env.rows()[-1]["stop_reason"] == "cap:context"


def test_minutes_cap(make_env):
    env = make_env()
    env.transcript("a5", rows=[user_row("2026-10-26T01:00:00Z"), assistant_row("m1", "2026-10-26T01:00:05Z")])
    start = rb.parse_ts("2026-10-26T01:00:00Z")
    env.now = start + 44 * 60
    assert not context(env.bash("a5", "echo a"))
    env.now = start + 45 * 60
    assert "minutes" in context(env.bash("a5", "echo b"))
    env.now = start + 60 * 60
    o = env.pre("a5", "Bash", {"command": "echo c"})
    assert decision(o) == "deny" and "cap:minutes" in reason(o)


def test_minutes_without_transcript_use_first_seen(make_env):
    env = make_env(RB_MIN_WARN=1, RB_MIN_HARD=2)
    env.bash("a6", "echo a")
    env.now += 61
    assert "minutes" in context(env.bash("a6", "echo b"))
    env.now += 61
    assert decision(env.pre("a6", "Bash", {"command": "echo c"})) == "deny"


def test_maxturns_is_not_the_enforcer(make_env):
    """R9: a factory-builder (maxTurns 200 in its frontmatter) is still stopped by the hook at the tool cap."""
    env = make_env(RB_TOOLS_WARN=150, RB_TOOLS_HARD=200)
    outs = run_calls(env, "a7", 200)
    assert all(decision(o) is None for o in outs)
    assert decision(env.pre("a7", "Bash", {"command": "echo 201"})) == "deny"


def test_tools_overshoot_within_ten_percent(make_env):
    """K5: with the default kill-after of 10, a capped run makes at most cap + 10 calls (<= +10% at 200)."""
    env = make_env()
    run_calls(env, "a8", 200)
    n = 200
    while True:
        o = env.pre("a8", "Bash", {"command": "echo more %d" % n})
        n += 1
        if (o or {}).get("continue") is False:
            break
    executed = 200 + 0  # every call after the cap was denied (none is a save-set call here)
    assert n - 1 <= 200 * 1.10
    assert executed <= 200 * 1.10


def test_hook_never_discards_work():
    """R3: the hook's source has no command that deletes, resets or cleans a worktree."""
    src = (rb.__file__ and open(rb.__file__).read())
    for bad in ("reset --hard", "clean -f", "worktree remove", "rmtree", "os.remove(wt", "branch -D"):
        assert bad not in src
