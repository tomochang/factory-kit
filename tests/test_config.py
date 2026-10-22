"""Config from factory.env (R2) and the files generated from templates (R1, R3).

Reference behaviour copied (T13-REFERENCE.md 1-3): one per-user file outside the repo, real environment wins,
a missing value is reported with the file's name, and generated configs never carry the token.
"""
import json
import os
import sys
from pathlib import Path

import pytest

from conftest import base_env, run, setup_install

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))


def kitenv(kit, env, *args):
    return run([sys.executable, str(kit / "lib" / "kitenv.py"), *args], env)


def test_values_come_from_factory_env_with_home_expanded(tmp_path):
    home, kit, env = setup_install(tmp_path)
    out = kitenv(kit, env, "json")
    assert out.returncode == 0, out.stderr
    cfg = json.loads(out.stdout)
    assert cfg["OWNER_NAME"] == "Alex Example"
    assert cfg["CHAT_ID"] == "!self:beeper.local"
    assert cfg["TOKEN_FILE"] == f"{home}/.config/factory-kit/beeper-token"
    assert cfg["WORK_DIR"] == f"{home}/factory"
    assert cfg["ASK_ME_STATE"] == f"{home}/.factory-kit/ask-me"
    assert cfg["TZ"] == "UTC"
    assert cfg["KIT"] == str(kit)
    # the kit itself is always protected, so the factory can't edit its own guards or the watchdog pin
    assert cfg["PROTECTED_DIRS"] == [f"{home}/private-repo", str(kit)]


def test_real_environment_wins_and_specific_names_win_over_generic_ones(tmp_path):
    _, kit, env = setup_install(tmp_path)
    env.update(BEEPER_BASE="http://127.0.0.1:1111", OWNER_NAME="Sam Other")
    cfg = json.loads(kitenv(kit, env, "json").stdout)
    assert cfg["BEEPER_BASE"] == "http://127.0.0.1:1111" and cfg["OWNER_NAME"] == "Sam Other"
    env["BEEPER_API_BASE"] = "http://127.0.0.1:2222"   # the name the scripts' tests have always used
    env["ASK_ME_CHAT"] = "!other:beeper.local"
    cfg = json.loads(kitenv(kit, env, "json").stdout)
    assert cfg["BEEPER_BASE"] == "http://127.0.0.1:2222" and cfg["CHAT_ID"] == "!other:beeper.local"


def test_defaults_when_optional_values_are_missing(tmp_path):
    home, kit, env = setup_install(tmp_path)
    (kit / "factory.env").write_text('OWNER_NAME="A B"\nCHAT_ID="!self:beeper.local"\n'
                                     'TOKEN_FILE="$HOME/.config/factory-kit/beeper-token"\n')
    cfg = json.loads(kitenv(kit, env, "json").stdout)
    assert cfg["BEEPER_BASE"] == "http://127.0.0.1:23373"
    assert cfg["WORK_DIR"] == f"{home}/factory"
    assert cfg["ASK_ME_STATE"] == f"{home}/.factory-kit/ask-me"
    assert cfg["PROTECTED_DIRS"] == [str(kit)]
    assert kitenv(kit, env, "get", "TZ").stdout.strip() == "UTC"


@pytest.mark.parametrize("missing", ["OWNER_NAME", "CHAT_ID", "TOKEN_FILE"])
def test_check_names_the_missing_value_and_the_file(tmp_path, missing):
    _, kit, env = setup_install(tmp_path)
    text = "\n".join(l for l in (kit / "factory.env").read_text().splitlines() if not l.startswith(missing))
    (kit / "factory.env").write_text(text + "\n")
    out = kitenv(kit, env, "check")
    assert out.returncode == 2
    assert missing in out.stderr and "factory.env" in out.stderr


def test_check_without_factory_env_points_at_the_example(tmp_path):
    _, kit, env = setup_install(tmp_path)
    (kit / "factory.env").unlink()
    out = kitenv(kit, env, "check")
    assert out.returncode == 2 and "factory.env.example" in out.stderr


def render(kit, env):
    out = run([sys.executable, str(kit / "lib" / "render.py")], env)
    assert out.returncode == 0, out.stderr
    return kit / "generated"


def test_generated_mcp_config_points_at_the_kit_channel_and_holds_no_secret(tmp_path):
    home, kit, env = setup_install(tmp_path)
    gen = render(kit, env)
    text = (gen / "mcp.json").read_text()
    server = json.loads(text)["mcpServers"]["whatsapp"]
    assert os.path.isabs(server["command"]) and Path(server["command"]).name.startswith("node")
    assert server["args"] == [str(kit / "foreman" / "channel" / "whatsapp-channel.mjs")]
    assert Path(server["args"][0]).is_file()
    assert server["env"]["FACTORY_ENV"] == str(kit / "factory.env")
    assert server["env"]["FACTORY_INSTRUCTIONS"] == str(gen / "channel-instructions.txt")
    assert "fake-token-for-tests" not in text and "!self:beeper.local" not in text


def commands(hooks_json, event, matcher=None):
    out = []
    for group in hooks_json["hooks"][event]:
        if matcher is None or group.get("matcher") == matcher:
            out += [h["command"] for h in group["hooks"]]
    return out


def test_workspace_settings_run_the_kit_guards_for_claude_code(tmp_path):
    _, kit, env = setup_install(tmp_path)
    s = json.loads((render(kit, env) / "workspace-settings.json").read_text())
    bash = commands(s, "PreToolUse", "Bash")
    write = commands(s, "PreToolUse", "Write|Edit|MultiEdit|NotebookEdit")
    assert any(str(kit / "hooks" / "security-bash.sh") in c for c in bash)
    assert any(str(kit / "foreman" / "factory_guard.py") in c for c in bash)
    assert any(str(kit / "hooks" / "security-file-write.sh") in c for c in write)
    assert any(str(kit / "foreman" / "factory_guard.py") in c for c in write)


def test_codex_hooks_use_the_same_guards_and_match_apply_patch(tmp_path):
    _, kit, env = setup_install(tmp_path)
    h = json.loads((render(kit, env) / "codex-hooks.json").read_text())
    file_groups = [g for g in h["hooks"]["PreToolUse"] if "apply_patch" in g.get("matcher", "")]
    assert file_groups, "no PreToolUse group matches apply_patch"
    cmds = [x["command"] for g in file_groups for x in g["hooks"]]
    assert any(str(kit / "hooks" / "security-file-write.sh") in c for c in cmds)
    assert any(str(kit / "foreman" / "factory_guard.py") in c for c in cmds)
    assert any(str(kit / "foreman" / "factory_guard.py") in c for c in commands(h, "PreToolUse", "Bash"))
    assert any(str(kit / "hooks" / "ask_me_stop.py") in c for c in commands(h, "Stop"))


def test_claude_stop_hook_fragment(tmp_path):
    _, kit, env = setup_install(tmp_path)
    h = json.loads((render(kit, env) / "claude-settings.json").read_text())
    assert any(str(kit / "hooks" / "ask_me_stop.py") in c for c in commands(h, "Stop"))
    assert "PreToolUse" not in h["hooks"]  # guards are workspace-scoped for Claude Code


def test_channel_instructions_are_filled_from_the_template(tmp_path):
    home, kit, env = setup_install(tmp_path)
    text = (render(kit, env) / "channel-instructions.txt").read_text()
    assert "{{" not in text and "}}" not in text
    assert "Alex Example" in text
    assert f"{home}/factory" in text
    assert f"{home}/private-repo" in text
    assert str(kit / "scripts" / "ask-me.py") in text
    assert str(kit / "scripts" / "safe-push.sh") in text
    assert str(kit / "foreman" / "codex-run.sh") in text


def test_an_unknown_placeholder_is_an_error():
    import render as r
    with pytest.raises(KeyError):
        r.render_text("hello {{NOPE}}", {"KIT": "/k"})
    assert r.render_text("a {{KIT}} b", {"KIT": "/k"}) == "a /k b"
