"""T18 R4: the Codex Stop hook (hooks/codex_watch_stop.py) and its install, trust step and doctor check.

The hook only records the finished turn (session, turn, cwd, the first line of the last message) in
<ASK_ME_STATE>/codex-watch/stops/ for the watchdog; it never sends, never blocks and never prints.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from conftest import KIT, run, setup_install

HOOK = KIT / "hooks" / "codex_watch_stop.py"


def call(tmp_path, payload, raw=None):
    env = dict(os.environ, ASK_ME_STATE=str(tmp_path / "state"), FACTORY_ENV=str(tmp_path / "none.env"))
    return subprocess.run([sys.executable, str(HOOK)], input=raw if raw is not None else json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=10)


def stops(tmp_path):
    d = tmp_path / "state" / "codex-watch" / "stops"
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


STOP = {"session_id": "01a15f9d-d3fd-78f2-a879-c4e87d83cb49", "turn_id": "01a15fd0-9a13-7e50-9ce7-f10b78c7de92",
        "cwd": "/private/tmp/t9-inbox-zero", "hook_event_name": "Stop", "model": "gpt-5.5",
        "transcript_path": None, "stop_hook_active": False, "permission_mode": "default",
        "last_assistant_message": "T9 learnings written; tests pass.\n\nHere is the full prompt text: secret plan"}


def test_records_the_turn_and_only_the_first_line(tmp_path):
    out = call(tmp_path, STOP)
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""
    [rec] = stops(tmp_path)
    assert rec["session_id"] == STOP["session_id"] and rec["turn_id"] == STOP["turn_id"]
    assert rec["cwd"] == "/private/tmp/t9-inbox-zero"
    assert rec["first_line"] == "T9 learnings written; tests pass."
    assert "secret plan" not in json.dumps(rec) and "prompt" not in json.dumps(rec)
    assert isinstance(rec["time"], float)


def test_record_is_private(tmp_path):
    call(tmp_path, STOP)
    d = tmp_path / "state" / "codex-watch" / "stops"
    f = next(d.glob("*.json"))
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_secrets_in_the_first_line_are_redacted(tmp_path):
    call(tmp_path, dict(STOP, last_assistant_message="key sk-" + "z" * 40))
    [rec] = stops(tmp_path)
    assert "zzzz" not in rec["first_line"] and "[redacted]" in rec["first_line"]


def test_bad_input_is_ignored_quietly(tmp_path):
    for raw in ("", "not json", "[]", json.dumps({"turn_id": "x"})):
        out = call(tmp_path, None, raw=raw)
        assert out.returncode == 0 and out.stdout == ""
    assert stops(tmp_path) == []


def test_ids_cannot_escape_the_folder(tmp_path):
    call(tmp_path, dict(STOP, session_id="../../evil", turn_id="../x"))
    assert not (tmp_path / "evil").exists()
    [rec] = stops(tmp_path)
    assert rec["session_id"] == "../../evil"           # kept as data; the file name is sanitised


def test_never_blocks_and_never_sends(tmp_path):
    text = HOOK.read_text()
    assert "decision" not in text and "ask-me" not in text.replace("ask-me state", "")
    out = call(tmp_path, dict(STOP, stop_hook_active=True))
    assert out.returncode == 0 and out.stdout == ""


def test_hook_template_adds_a_second_stop_entry_beside_ask_me_stop():
    data = json.loads((KIT / "templates" / "codex-hooks.json.tmpl").read_text())
    cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
    assert any("ask_me_stop.py" in c for c in cmds)
    assert any("codex_watch_stop.py" in c for c in cmds)
    groups = data["hooks"]["Stop"]
    assert len(groups) == 2 and "codex_watch_stop.py" in groups[1]["hooks"][0]["command"]
    claude = (KIT / "templates" / "claude-settings.json.tmpl").read_text()
    assert "codex_watch_stop" not in claude                     # Codex only


def kit_cmds(path, kit):
    data = json.loads(Path(path).read_text())
    return [h["command"] for groups in data.get("hooks", {}).values() for g in groups for h in g["hooks"]
            if str(kit) in h["command"]]


def test_install_registers_the_hook_and_prints_the_trust_step(tmp_path):
    home, kit, env = setup_install(tmp_path)
    dry = run(["bash", str(kit / "install.sh")], env, timeout=180)
    assert str(kit / "hooks" / "codex_watch_stop.py") in dry.stdout
    out = run(["bash", str(kit / "install.sh"), "--apply"], env, timeout=180)
    assert out.returncode == 0, out.stdout + out.stderr
    assert any("codex_watch_stop.py" in c for c in kit_cmds(home / ".codex" / "hooks.json", kit))
    assert not any("codex_watch_stop.py" in c for c in kit_cmds(home / ".claude" / "settings.json", kit))
    assert "/hooks" in out.stdout and "codex_watch_stop.py" in out.stdout


def test_an_existing_install_gets_the_hook_appended_without_moving_trusted_ones(tmp_path):
    home, kit, env = setup_install(tmp_path)
    old = {"hooks": {"Stop": [{"hooks": [{"type": "command", "timeout": 10, "command":
           f"test -f '{kit}/hooks/ask_me_stop.py' && python3 '{kit}/hooks/ask_me_stop.py' || true"}]}]}}
    (home / ".codex").mkdir()
    (home / ".codex" / "hooks.json").write_text(json.dumps(old))
    assert run(["bash", str(kit / "install.sh"), "--apply"], env, timeout=180).returncode == 0
    stop = json.loads((home / ".codex" / "hooks.json").read_text())["hooks"]["Stop"]
    assert "ask_me_stop.py" in stop[0]["hooks"][0]["command"]          # position 0:0 kept (trust record)
    assert any("codex_watch_stop.py" in h["command"] for g in stop[1:] for h in g["hooks"])


def test_installed_hook_command_runs(tmp_path):
    home, kit, env = setup_install(tmp_path)
    assert run(["bash", str(kit / "install.sh"), "--apply"], env, timeout=180).returncode == 0
    stop = json.loads((home / ".codex" / "hooks.json").read_text())["hooks"]["Stop"]
    cmd = next(h["command"] for g in stop for h in g["hooks"] if "codex_watch_stop.py" in h["command"])
    r = subprocess.run(["sh", "-c", cmd], input=json.dumps(STOP), capture_output=True, text=True, env=env, timeout=10)
    assert r.returncode == 0 and r.stdout == ""
    assert list((home / ".factory-kit" / "ask-me" / "codex-watch" / "stops").glob("*.json"))


def test_uninstall_removes_the_hook(tmp_path):
    home, kit, env = setup_install(tmp_path)
    run(["bash", str(kit / "install.sh"), "--apply"], env, timeout=180)
    run(["bash", str(kit / "install.sh"), "--uninstall"], env, timeout=180)
    assert not any("codex_watch_stop.py" in c for c in kit_cmds(home / ".codex" / "hooks.json", kit))


def test_review1_hook_and_watchdog_redact_alike():
    sys.path.insert(0, str(KIT / "watchdog"))
    import re as _re
    hook = HOOK.read_text()
    wd = (KIT / "watchdog" / "watchdog.py").read_text()
    pat = lambda s: s[s.index("SECRET_RE = re.compile("):s.index("\n\n", s.index("SECRET_RE = re.compile("))]
    assert pat(hook) == pat(wd)


def test_review1_a_broken_lib_still_prints_nothing(tmp_path):
    import shutil
    kit = tmp_path / "kit"
    (kit / "hooks").mkdir(parents=True)
    shutil.copy(HOOK, kit / "hooks" / "codex_watch_stop.py")          # no lib/ next to it
    r = subprocess.run([sys.executable, str(kit / "hooks" / "codex_watch_stop.py")], input=json.dumps(STOP),
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
