"""Stall rules (SPEC R4, R5; KPI K4; Done-when 2).

Same tool + identical input + identical result: the 3rd time is denied with a "change course" reason, the 6th time
blocks the run (stall:repeat). Same tool + input + error 3 times in a row: block (stall:error). A changed result or a
success resets. Polling commands whose result changes are never stopped (OpenHands SDK #762: polling every 5 minutes
for 15 minutes tripped its detector).
"""
import pytest

from rb_helpers import env, make_env, context, decision, reason


def test_identical_call_third_denied_sixth_stops(env):
    outs = [env.bash("r1", "echo x", stdout="x") for _ in range(6)]
    assert decision(outs[0]) is None and decision(outs[1]) is None
    for o in outs[2:5]:
        assert decision(o) == "deny"
        assert "different approach" in reason(o) and "stall:repeat" not in reason(o)
    assert decision(outs[5]) == "deny" and "stall:repeat" in reason(outs[5]) and "PARTIAL.md" in reason(outs[5])
    # the run is now in the block stage: other work is denied too
    assert decision(env.pre("r1", "Bash", {"command": "ls"})) == "deny"
    env.stop("r1")
    assert env.rows()[-1]["stop_reason"] == "stall:repeat"


def test_hand_back_after_repeat_deny_is_a_stall_stop(env):
    """Live check (M4): the run handed back right after its 3rd identical call was denied: stall:repeat."""
    for _ in range(3):
        env.bash("r0", "echo x", stdout="x")
    env.pre("r0", "SubagentHandback", {"message": "STOPPED: repeated call denied"})
    env.stop("r0")
    assert env.rows()[-1]["stop_reason"] == "stall:repeat"


def test_comment_or_spacing_does_not_make_a_new_call(env):
    """Live check (M4): the run appended "# call 3 of 10" to dodge the repeat rule; comments do not change a call."""
    outs = [env.bash("rc", "echo x" + ("  # call %d of 10" % i if i > 1 else ""), stdout="x") for i in range(1, 7)]
    assert [decision(o) for o in outs[:2]] == [None, None]
    assert all(decision(o) == "deny" for o in outs[2:])
    assert "stall:repeat" in reason(outs[5])
    assert decision(env.bash("rc2", "echo '#not a comment'", stdout="#")) is None


def test_changed_result_resets(env):
    outs = [env.bash("r2", "date", stdout="tick %d" % i) for i in range(10)]
    assert all(decision(o) is None for o in outs)


def test_other_call_in_between_resets(env):
    outs = [env.bash("r3", c, stdout="same") for c in ["echo a", "echo a", "echo b", "echo a", "echo a", "echo b"]]
    assert all(decision(o) is None for o in outs)


def test_denied_repeats_then_new_call_resets(env):
    for _ in range(3):
        env.bash("r5", "echo x", stdout="x")
    assert decision(env.bash("r5", "echo y", stdout="y")) is None
    assert decision(env.bash("r5", "echo x", stdout="x")) is None  # streak broken by echo y


def test_same_error_three_times_stops(env):
    for i in range(3):
        o = env.bash("e1", "npm test", fail="Exit code 1\nError: Cannot find module 'express'")
        assert decision(o) is None
    o = env.pre("e1", "Bash", {"command": "npm install"})
    assert decision(o) == "deny" and "stall:error" in reason(o)
    env.stop("e1")
    assert env.rows()[-1]["stop_reason"] == "stall:error"


def test_error_streak_reset_by_success_or_changed_error(env):
    env.bash("e2", "npm test", fail="Exit code 1\nA")
    env.bash("e2", "npm test", fail="Exit code 1\nA")
    env.bash("e2", "npm test", stdout="passed")
    env.bash("e2", "npm test", fail="Exit code 1\nA")
    env.bash("e2", "npm test", fail="Exit code 1\nB")
    assert decision(env.pre("e2", "Bash", {"command": "npm test"})) is None


def test_openhands_762_polling_case(env):
    """Poll 4 times, 5 minutes apart, output changing: never stopped, never warned."""
    for i in range(4):
        o = env.bash("p1", "gh pr checks 12 --repo o/r", stdout="test\tpending\t%dm\thttps://x" % (i * 5))
        assert decision(o) is None and not context(o)
        env.now += 300


def test_pending_checks_failure_exit_is_not_an_error_streak(env):
    """`gh pr checks` exits 8 while checks are pending; that is polling, not a failing call."""
    for i in range(4):
        o = env.bash("p2", "gh pr checks 12 --repo o/r", fail="Exit code 8\ntest\tpending\t%ds" % (i * 30))
        assert decision(o) is None
        env.now += 30


def test_sleep_never_counts(env):
    for _ in range(8):
        assert decision(env.bash("p3", "sleep 300", stdout="")) is None


def test_polling_with_identical_result_still_counts(env):
    outs = [env.bash("p4", "gh pr checks 12 --repo o/r", stdout="test\tpass\t1m") for _ in range(3)]
    assert decision(outs[2]) == "deny"


def test_long_output_is_not_a_loop(env):
    """Cursor forum false positive: long legitimate output (big files, long logs) must not trip anything."""
    for i in range(5):
        o = env.bash("l1", "cat build-%d.log" % i, stdout=("line %d\n" % i) * 50000)
        assert decision(o) is None


@pytest.mark.parametrize("push", ["git push -u origin t33", "scripts/safe-push.sh"])
def test_ci_rounds(env, push):
    env.bash("c1", push, stdout="pushed")
    env.bash("c1", "gh pr checks 5 --repo o/r", fail="Exit code 1\ntest\tfail\t1m")
    assert decision(env.pre("c1", "Bash", {"command": "pytest -q"})) is None  # one failing round is fine
    env.post("c1", "Bash", {"command": "pytest -q"}, {"stdout": "fixed"})
    env.bash("c1", push, stdout="pushed")
    env.bash("c1", "gh pr checks 5 --repo o/r", stdout="test\tfail\t2m")
    o = env.pre("c1", "Bash", {"command": "pytest -q"})
    assert decision(o) == "deny" and "ci-rounds" in reason(o)


def test_ci_pending_and_passing_do_not_count(env):
    for i in range(3):
        env.bash("c2", "git push", stdout="pushed %d" % i)
        env.bash("c2", "gh pr checks 5 --repo o/r", fail="Exit code 8\ntest\tpending\t%ds" % i)
        env.bash("c2", "gh pr checks 5 --repo o/r", stdout="test\tpass\t%ds" % i)
    assert decision(env.pre("c2", "Bash", {"command": "echo ok"})) is None


def test_safe_push_test_failure_counts_as_round(env):
    env.bash("c3", "scripts/safe-push.sh", fail="Exit code 4\ntests failed")
    env.bash("c3", "echo fix", stdout="fix")
    env.bash("c3", "scripts/safe-push.sh", fail="Exit code 4\ntests failed again")
    assert "ci-rounds" in reason(env.pre("c3", "Bash", {"command": "echo next"}))
