"""doctor.sh against a fake Beeper (R6): Beeper reachable, own-chat check, hook install and Codex trust,
Claude/Codex versions, the dev-channel flag, the watchdog. Exit 1 only on a FAIL."""
import json

from conftest import fake_beeper, run, setup_install


def doctor(kit, env):
    return run(["bash", str(kit / "doctor.sh")], env, timeout=120)


def installed(tmp_path, port, **kw):
    home, kit, env = setup_install(tmp_path, port=port, **kw)
    out = run(["bash", str(kit / "install.sh"), "--apply"], env, timeout=180)
    assert out.returncode == 0, out.stdout + out.stderr
    return home, kit, env


def test_a_fresh_install_passes(tmp_path, beeper):
    home, kit, env = installed(tmp_path, beeper.port)
    out = doctor(kit, env)
    assert out.returncode == 0, out.stdout + out.stderr
    for line in ("PASS factory.env", "PASS token file", "PASS Beeper reachable", "PASS own chat",
                 "PASS Claude Code 2.1.307", "PASS Codex codex-cli 0.163.0", "PASS Claude hooks", "PASS Codex hooks",
                 "PASS dev channel"):
        assert line in out.stdout, (line, out.stdout)
    assert "WARN Codex hook trust" in out.stdout and "/hooks" in out.stdout
    assert "WARN watchdog" in out.stdout
    assert beeper.sent == []                                  # doctor never sends
    assert "Bearer fake-token-for-tests" in beeper.auth       # it used the configured token
    assert "fake-token-for-tests" not in out.stdout


def test_not_the_own_chat_fails(tmp_path):
    fake = fake_beeper.Fake(self_phone="+819999999999")
    srv = fake_beeper.serve(fake)
    try:
        home, kit, env = installed(tmp_path, srv.server_port)
        out = doctor(kit, env)
        assert out.returncode == 1
        assert "FAIL own chat" in out.stdout
        assert fake.sent == []
    finally:
        srv.shutdown()


def test_beeper_down_fails(tmp_path, beeper):
    fake = fake_beeper.Fake()
    srv = fake_beeper.serve(fake)
    port = srv.server_port
    srv.shutdown()
    srv.server_close()
    home, kit, env = installed(tmp_path, port)
    out = doctor(kit, env)
    assert out.returncode == 1 and "FAIL Beeper reachable" in out.stdout


def test_hooks_not_installed_fails_with_the_next_step(tmp_path, beeper):
    home, kit, env = setup_install(tmp_path, port=beeper.port)
    out = doctor(kit, env)
    assert out.returncode == 1
    assert "FAIL Claude hooks" in out.stdout and "install.sh --apply" in out.stdout


def test_broken_claude_fails(tmp_path, beeper):
    home, kit, env = installed(tmp_path, beeper.port)
    bad = tmp_path / "badbin"
    bad.mkdir()
    (bad / "claude").write_text("#!/bin/sh\nexit 1\n")
    (bad / "claude").chmod(0o755)
    env["PATH"] = f"{bad}:{env['PATH']}"
    out = doctor(kit, env)
    assert out.returncode == 1 and "FAIL Claude Code" in out.stdout


def test_trusted_codex_hooks_pass(tmp_path, beeper):
    home, kit, env = installed(tmp_path, beeper.port)
    hooks_file = home / ".codex" / "hooks.json"
    data = json.loads(hooks_file.read_text())
    snake = {"PreToolUse": "pre_tool_use", "Stop": "stop"}
    lines = ["[hooks.state]", ""]
    for event, groups in data["hooks"].items():
        for gi, g in enumerate(groups):
            for hi, _ in enumerate(g["hooks"]):
                lines += [f'[hooks.state."{hooks_file}:{snake[event]}:{gi}:{hi}"]', 'trusted_hash = "sha256:abc"', ""]
    (home / ".codex" / "config.toml").write_text("\n".join(lines))
    out = doctor(kit, env)
    assert out.returncode == 0, out.stdout
    assert "PASS Codex hook trust" in out.stdout


def test_loose_token_permissions_warn(tmp_path, beeper):
    home, kit, env = installed(tmp_path, beeper.port)
    (home / ".config" / "factory-kit" / "beeper-token").chmod(0o644)
    out = doctor(kit, env)
    assert "WARN token file" in out.stdout and "chmod 600" in out.stdout
