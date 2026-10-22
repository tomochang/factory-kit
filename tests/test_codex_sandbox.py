"""codex-run.sh and the Codex sandbox (T15, changed on the owner's Q22 answer "C"): opt-in, off by default.

CODEX_SANDBOX=1 in factory.env adds `--sandbox workspace-write`; without it (or with 0) the build starts as before
T15. With it on, git commit and ask-me don't work inside the build (factory.env.example says so).
Uses a fake codex binary; nothing real is started.
"""
from conftest import KIT, run, setup_install


def codex_args(tmp_path, sandbox=None):
    home, kit, env = setup_install(tmp_path)
    if sandbox is not None:
        with open(kit / "factory.env", "a") as f:
            f.write(f'CODEX_SANDBOX="{sandbox}"\n')
    wt = tmp_path / "t2-wt"
    wt.mkdir()
    task = tmp_path / "t2.md"
    task.write_text("Build it.\n")
    out = run(["bash", str(kit / "foreman" / "codex-run.sh"), str(wt), str(task), "--model", "gpt-5.5"], env)
    assert out.returncode == 0, out.stdout + out.stderr
    log = (tmp_path / "bin" / "calls.log").read_text().splitlines()
    return [l[4:] for l in log if l.startswith("arg=")], wt.resolve()


def test_codex_sandbox_is_off_by_default(tmp_path):
    args, wt = codex_args(tmp_path)
    assert args == ["exec", "--cd", str(wt), "--model", "gpt-5.5", "-"]


def test_codex_sandbox_off_when_set_to_0(tmp_path):
    args, wt = codex_args(tmp_path, "0")
    assert "--sandbox" not in args


def test_codex_sandbox_on_adds_workspace_write(tmp_path):
    args, wt = codex_args(tmp_path, "1")
    assert args == ["exec", "--cd", str(wt), "--sandbox", "workspace-write", "--model", "gpt-5.5", "-"]


def test_the_example_config_documents_the_switch_and_its_cost():
    text = (KIT / "factory.env.example").read_text()
    assert 'CODEX_SANDBOX="0"' in text
    block = text[text.index("CODEX_SANDBOX") - 400:text.index('CODEX_SANDBOX="0"')]
    assert "git commit" in block and "ask-me" in block


def test_no_sandboxed_claude_build_starter_is_shipped():
    """Q22 "A": the kit starts Claude builds as before T15 (background agents of the foreman)."""
    assert not (KIT / "foreman" / "claude-run.sh").exists()
    assert not (KIT / "templates" / "build-settings.json.tmpl").exists()
    assert "CLAUDE_RUN" not in (KIT / "templates" / "channel-instructions.txt.tmpl").read_text()
