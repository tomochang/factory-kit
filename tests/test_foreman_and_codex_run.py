"""foreman.sh starts the Claude Code foreman with the generated configs; codex-run.sh starts a Codex build in a
worktree with `command codex exec --cd` (R3). Both use fake claude/codex binaries."""
import json

from conftest import run, setup_install


def calls(tmp_path):
    p = tmp_path / "bin" / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


def test_foreman_starts_claude_with_the_channel_and_the_kit_guards(tmp_path):
    home, kit, env = setup_install(tmp_path)
    assert run(["bash", str(kit / "install.sh"), "--apply"], env, timeout=180).returncode == 0
    env["FACTORY_WATCHDOG"] = "off"
    out = run(["bash", str(kit / "foreman" / "foreman.sh"), "--model", "opus"], env, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    log = calls(tmp_path)
    assert log[0] == f"claude cwd={home / 'factory'} session=1"
    args = [l[4:] for l in log if l.startswith("arg=")]
    assert args[:4] == ["--mcp-config", str(kit / "generated" / "mcp.json"),
                        "--dangerously-load-development-channels", "server:whatsapp"]
    assert args[4:] == ["--model", "opus"]
    ws = json.loads((home / "factory" / ".claude" / "settings.json").read_text())
    assert ws == json.loads((kit / "generated" / "workspace-settings.json").read_text())


def test_foreman_before_install_says_what_to_run(tmp_path):
    home, kit, env = setup_install(tmp_path)
    env["FACTORY_WATCHDOG"] = "off"
    out = run(["bash", str(kit / "foreman" / "foreman.sh")], env)
    assert out.returncode == 2 and "install.sh --apply" in out.stderr
    assert calls(tmp_path) == []


def worktree(tmp_path, name="t1-wt"):
    wt = tmp_path / name
    wt.mkdir()
    task = tmp_path / f"{name}.md"
    task.write_text("Build the word counter. Run the tests.\n")
    return wt, task


def test_codex_run_starts_codex_exec_in_the_worktree(tmp_path):
    home, kit, env = setup_install(tmp_path)
    wt, task = worktree(tmp_path)
    out = run(["bash", str(kit / "foreman" / "codex-run.sh"), str(wt), str(task), "--model", "gpt-5.5"], env)
    assert out.returncode == 0, out.stdout + out.stderr
    log = calls(tmp_path)
    assert log[0].startswith("codex ") and log[0].endswith("session=1")
    args = [l[4:] for l in log if l.startswith("arg=")]
    assert args == ["exec", "--json", "-o", str(task.resolve().parent / "t1-wt.codex-last.md"),  # T18 R5
                    "--cd", str(wt.resolve()), "--model", "gpt-5.5", "-"]
    assert (tmp_path / "bin" / "codex.stdin").read_text() == task.read_text()
    assert "command codex" in (kit / "foreman" / "codex-run.sh").read_text()  # never a shell function


def test_codex_run_refuses_bad_targets(tmp_path):
    home, kit, env = setup_install(tmp_path)
    wt, task = worktree(tmp_path)
    script = str(kit / "foreman" / "codex-run.sh")
    assert run(["bash", script, str(tmp_path / "missing"), str(task)], env).returncode == 2
    assert run(["bash", script, str(wt), str(tmp_path / "missing.md")], env).returncode == 2
    prot = home / "private-repo"
    prot.mkdir()
    out = run(["bash", script, str(prot), str(task)], env)
    assert out.returncode == 2 and "protected" in out.stderr
    out = run(["bash", script, str(kit), str(task)], env)
    assert out.returncode == 2
    assert calls(tmp_path) == []
