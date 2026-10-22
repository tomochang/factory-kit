"""T18 R5 and the doctor check: codex-run.sh runs `codex exec --json`, keeps the JSONL beside the task and the
exit code, and tells the owner "Codex build failed: <task>" on exit != 0, turn.failed or error. doctor.sh shows
whether the Codex watch hook is installed and trusted and whether rollouts are readable.

ask-me talks only to the fake Beeper (tests/fake_beeper.py); codex is a fake script."""
import json

from conftest import run, setup_install

OK_EVENTS = [{"type": "thread.started", "thread_id": "th1"}, {"type": "turn.started"},
             {"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "Built it."}},
             {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0,
                                                  "output_tokens": 1, "reasoning_output_tokens": 0}}]
FAILED_EVENTS = [{"type": "thread.started", "thread_id": "th1"}, {"type": "turn.started"},
                 {"type": "turn.failed", "error": {"message": "stream disconnected sk-not-a-real-key"}}]
ERROR_EVENTS = [{"type": "thread.started", "thread_id": "th1"}, {"type": "error", "message": "boom"}]


def fake_codex(tmp_path, events, rc=0, last="Built it."):
    """A codex stand-in for `exec --json -o FILE`: prints the events as JSONL, writes FILE, exits rc."""
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    lines = "\\n".join(json.dumps(e).replace("'", "") for e in events)
    (b / "codex").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "codex-cli 0.163.0"; exit 0; fi\n'
        f'{{ for a in "$@"; do echo "arg=$a"; done; }} >> "{b}/calls.log"\n'
        'out=""; prev=""; for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
        f'[ -n "$out" ] && printf "%s\\n" "{last}" > "$out"\n'
        'cat > /dev/null\n'
        f"printf '{lines}\\n'\n"
        f"exit {rc}\n")
    (b / "codex").chmod(0o755)
    return b


def setup(tmp_path, beeper, events, rc=0):
    home, kit, env = setup_install(tmp_path, port=beeper.port)
    fake_codex(tmp_path, events, rc)
    wt = tmp_path / "t7-wt"
    wt.mkdir()
    task = tmp_path / "t7-task.md"
    task.write_text("Build the word counter.\n")
    return home, kit, env, wt, task


def codex_run(kit, env, wt, task, *extra):
    return run(["bash", str(kit / "foreman" / "codex-run.sh"), str(wt), str(task), *extra], env, timeout=60)


def sent_texts(beeper):
    return [t.split("\n", 1)[-1] for t in beeper.sent]   # ask-me puts "<icon> · <from>" on the first line


def args(tmp_path):
    return [l[4:] for l in (tmp_path / "bin" / "calls.log").read_text().splitlines() if l.startswith("arg=")]


def test_success_runs_exec_json_keeps_the_jsonl_and_sends_nothing(tmp_path, beeper):
    home, kit, env, wt, task = setup(tmp_path, beeper, OK_EVENTS)
    out = codex_run(kit, env, wt, task, "--model", "gpt-5.5")
    assert out.returncode == 0, out.stdout + out.stderr
    a = args(tmp_path)
    assert a[:2] == ["exec", "--json"] and "-o" in a and a[-1] == "-"
    assert a[a.index("--cd") + 1] == str(wt.resolve()) and a[-3:-1] == ["--model", "gpt-5.5"]
    jsonl = tmp_path / "t7-task.codex.jsonl"
    assert [json.loads(l)["type"] for l in jsonl.read_text().splitlines()] == [e["type"] for e in OK_EVENTS]
    assert "Built it." in out.stdout                         # the last message, for the foreman
    assert str(jsonl) in out.stdout + out.stderr
    assert sent_texts(beeper) == []                          # "finished" comes from the watchdog, not from here


def test_nonzero_exit_is_kept_and_told(tmp_path, beeper):
    home, kit, env, wt, task = setup(tmp_path, beeper, OK_EVENTS, rc=7)
    out = codex_run(kit, env, wt, task)
    assert out.returncode == 7
    assert sent_texts(beeper) == ["Codex build failed: t7-task (exit 7)"]


def test_turn_failed_with_exit_1_is_told_once_without_the_error_text(tmp_path, beeper):
    home, kit, env, wt, task = setup(tmp_path, beeper, FAILED_EVENTS, rc=1)
    out = codex_run(kit, env, wt, task)
    assert out.returncode == 1
    assert sent_texts(beeper) == ["Codex build failed: t7-task (exit 1, turn.failed)"]
    assert "sk-not" not in json.dumps(beeper.sent)


def test_error_event_with_exit_0_still_fails(tmp_path, beeper):
    home, kit, env, wt, task = setup(tmp_path, beeper, ERROR_EVENTS, rc=0)
    out = codex_run(kit, env, wt, task)
    assert out.returncode == 1
    assert sent_texts(beeper) == ["Codex build failed: t7-task (exit 0, error)"]


def test_send_failure_does_not_hide_the_exit_code(tmp_path):
    home, kit, env = setup_install(tmp_path, port=9)               # nothing listens: ask-me fails
    fake_codex(tmp_path, FAILED_EVENTS, rc=1)
    wt = tmp_path / "wt"
    wt.mkdir()
    task = tmp_path / "t8.md"
    task.write_text("x\n")
    out = codex_run(kit, env, wt, task)
    assert out.returncode == 1 and "could not send" in out.stderr


# --- doctor.sh

def installed(tmp_path, port):
    home, kit, env = setup_install(tmp_path, port=port)
    assert run(["bash", str(kit / "install.sh"), "--apply"], env, timeout=180).returncode == 0
    return home, kit, env


def doctor(kit, env):
    return run(["bash", str(kit / "doctor.sh")], env, timeout=120)


def trust_all(home):
    hooks_file = home / ".codex" / "hooks.json"
    data = json.loads(hooks_file.read_text())
    snake = {"PreToolUse": "pre_tool_use", "Stop": "stop"}
    lines = ["[hooks.state]", ""]
    for event, groups in data["hooks"].items():
        for gi, g in enumerate(groups):
            for hi, _ in enumerate(g["hooks"]):
                lines += [f'[hooks.state."{hooks_file}:{snake[event]}:{gi}:{hi}"]', 'trusted_hash = "sha256:abc"', ""]
    (home / ".codex" / "config.toml").write_text("\n".join(lines))


def rollout(home):
    d = home / ".codex" / "sessions" / "2026" / "10" / "22"
    d.mkdir(parents=True)
    (d / "rollout-2026-10-22T02-59-01-abc.jsonl").write_text(json.dumps(
        {"timestamp": "2026-10-21T17:59:25Z", "type": "session_meta", "payload": {"id": "abc", "cwd": "/w"}}) + "\n")


def test_doctor_warns_until_the_watch_hook_is_trusted(tmp_path, beeper):
    home, kit, env = installed(tmp_path, beeper.port)
    out = doctor(kit, env)
    assert "WARN Codex watch hook" in out.stdout and "/hooks" in out.stdout
    trust_all(home)
    out = doctor(kit, env)
    assert "PASS Codex watch hook" in out.stdout, out.stdout


def test_doctor_fails_when_the_watch_hook_is_missing(tmp_path, beeper):
    home, kit, env = installed(tmp_path, beeper.port)
    f = home / ".codex" / "hooks.json"
    data = json.loads(f.read_text())
    data["hooks"]["Stop"] = [g for g in data["hooks"]["Stop"]
                             if not any("codex_watch_stop.py" in h["command"] for h in g["hooks"])]
    f.write_text(json.dumps(data))
    out = doctor(kit, env)
    assert "FAIL Codex watch hook" in out.stdout and "install.sh --apply" in out.stdout


def test_doctor_reports_whether_rollouts_are_readable(tmp_path, beeper):
    home, kit, env = installed(tmp_path, beeper.port)
    out = doctor(kit, env)
    assert "WARN Codex rollouts" in out.stdout
    rollout(home)
    out = doctor(kit, env)
    assert "PASS Codex rollouts" in out.stdout and str(home / ".codex" / "sessions") in out.stdout
    assert beeper.sent == []
