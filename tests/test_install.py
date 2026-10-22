"""install.sh in a temporary HOME (R4, R5, KPI "skills in both tools").

Print mode writes nothing; --apply backs up and edits the global files, links each kit skill into
~/.claude/skills and ~/.agents/skills (skipping names that exist) and sets up the work folder;
--uninstall takes out only what the kit added.
"""
import json

from pathlib import Path

from conftest import run, setup_install

SKILLS = ["imozuru", "foreman", "ask-me", "safe-push"]


def install(kit, env, *args):
    return run(["bash", str(kit / "install.sh"), *args], env, timeout=180)


def tree(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))


def kit_commands(path, kit):
    data = json.loads(Path(path).read_text())
    return [h["command"] for groups in data.get("hooks", {}).values() for g in groups for h in g["hooks"]
            if str(kit) in h["command"]]


def test_print_mode_shows_every_change_and_writes_nothing(tmp_path):
    home, kit, env = setup_install(tmp_path)
    before = tree(home)
    out = install(kit, env)
    assert out.returncode == 0, out.stderr
    text = out.stdout
    assert f"would add to {home}/.claude/settings.json" in text
    assert f"would add to {home}/.codex/hooks.json" in text
    assert f"would add to {home}/.zshrc" in text
    assert str(kit / "hooks" / "ask_me_stop.py") in text          # the lines themselves are shown
    assert str(kit / "foreman" / "factory_guard.py") in text
    assert 'export FACTORY_KIT=' in text
    for s in SKILLS:
        assert f"would link {home}/.claude/skills/{s}" in text
        assert f"would link {home}/.agents/skills/{s}" in text
    assert "/hooks" in text and "trust" in text                  # the Codex hook trust step
    assert "--apply" in text
    assert tree(home) == before
    assert not (kit / "generated").exists()


def test_missing_factory_env_stops_with_the_next_step(tmp_path):
    home, kit, env = setup_install(tmp_path)
    (kit / "factory.env").unlink()
    out = install(kit, env, "--apply")
    assert out.returncode == 2
    assert "factory.env.example" in out.stderr
    assert not (home / ".claude").exists()


def test_apply_links_skills_for_both_tools_and_sets_up_the_factory(tmp_path):
    home, kit, env = setup_install(tmp_path)
    out = install(kit, env, "--apply")
    assert out.returncode == 0, out.stdout + out.stderr
    for s in SKILLS:
        for base in (home / ".claude" / "skills", home / ".agents" / "skills"):
            link = base / s
            assert link.is_symlink(), link
            assert link.resolve() == (kit / "skills" / s).resolve()
            assert (link / "SKILL.md").is_file()
    work = home / "factory"
    assert (work / "workflow").resolve() == (kit / "workflow").resolve()
    assert "WORKFLOW-remote.md" in (work / "CLAUDE.md").read_text()
    assert "WORKFLOW-remote.md" in (work / "AGENTS.md").read_text()
    assert "| T | Title | State | Date |" in (work / "TASKS.md").read_text()
    for f in ("mcp.json", "workspace-settings.json", "build-settings.json", "codex-hooks.json", "claude-settings.json",
              "channel-instructions.txt"):
        assert (kit / "generated" / f).is_file(), f
    assert any("ask_me_stop.py" in c for c in kit_commands(home / ".claude" / "settings.json", kit))
    codex = kit_commands(home / ".codex" / "hooks.json", kit)
    assert any("factory_guard.py" in c for c in codex) and any("security-file-write.sh" in c for c in codex)
    rc = (home / ".zshrc").read_text()
    assert "# >>> factory-kit >>>" in rc and f'export FACTORY_KIT="{kit}"' in rc
    assert "/hooks" in out.stdout                                # trust step printed after apply too


def test_apply_skips_existing_skill_names_and_keeps_existing_settings_with_a_backup(tmp_path):
    home, kit, env = setup_install(tmp_path)
    mine = home / ".claude" / "skills" / "imozuru"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: imozuru\ndescription: my own copy\n---\n")
    settings = home / ".claude" / "settings.json"
    original = {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
    settings.write_text(json.dumps(original))
    (home / ".codex").mkdir()
    (home / ".codex" / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo codex-mine"}]}]}}))
    (home / ".zshrc").write_text("export PATH=/opt/bin:$PATH\n")
    out = install(kit, env, "--apply")
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"skip: {mine} exists" in out.stdout
    assert not mine.is_symlink() and "my own copy" in (mine / "SKILL.md").read_text()
    assert (home / ".agents" / "skills" / "imozuru").is_symlink()   # the other tool still gets it
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert any(h["command"] == "echo mine" for g in data["hooks"]["Stop"] for h in g["hooks"])
    backups = list((home / ".claude").glob("settings.json.factory-kit-backup-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == original
    assert list((home / ".codex").glob("hooks.json.factory-kit-backup-*"))
    assert list(home.glob(".zshrc.factory-kit-backup-*"))
    codex = json.loads((home / ".codex" / "hooks.json").read_text())
    assert any(h["command"] == "echo codex-mine" for g in codex["hooks"]["PreToolUse"] for h in g["hooks"])
    assert (home / ".zshrc").read_text().startswith("export PATH=/opt/bin:$PATH\n")


def test_apply_twice_adds_nothing_twice(tmp_path):
    home, kit, env = setup_install(tmp_path)
    assert install(kit, env, "--apply").returncode == 0
    first = (kit_commands(home / ".claude" / "settings.json", kit), kit_commands(home / ".codex" / "hooks.json", kit),
             (home / ".zshrc").read_text())
    out = install(kit, env, "--apply")
    assert out.returncode == 0, out.stderr
    second = (kit_commands(home / ".claude" / "settings.json", kit),
              kit_commands(home / ".codex" / "hooks.json", kit), (home / ".zshrc").read_text())
    assert first == second


def test_uninstall_removes_only_what_the_kit_added(tmp_path):
    home, kit, env = setup_install(tmp_path)
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps(
        {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}))
    mine = home / ".agents" / "skills" / "safe-push"
    mine.mkdir(parents=True)
    assert install(kit, env, "--apply").returncode == 0
    (home / "factory" / "TASKS.md").write_text("| T1 | my task | done | 2026-10-22 |\n")
    out = install(kit, env, "--uninstall")
    assert out.returncode == 0, out.stdout + out.stderr
    data = json.loads((home / ".claude" / "settings.json").read_text())
    assert data == {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
    assert kit_commands(home / ".codex" / "hooks.json", kit) == []
    for s in SKILLS:
        assert not (home / ".claude" / "skills" / s).exists()
    assert mine.is_dir() and not mine.is_symlink()                # not ours: kept
    assert "factory-kit" not in (home / ".zshrc").read_text()
    assert "my task" in (home / "factory" / "TASKS.md").read_text()  # the owner's data stays
    assert not (kit / "generated").exists()


def test_a_codex_shell_function_gets_a_warning(tmp_path):
    home, kit, env = setup_install(tmp_path)
    (home / ".zshrc").write_text('codex() {\n  command codex --cd "$HOME/somewhere" "$@"\n}\n')
    out = install(kit, env)
    assert "codex()" in out.stdout and "command codex" in out.stdout


def test_token_file_mode_is_checked(tmp_path):
    home, kit, env = setup_install(tmp_path)
    (home / ".config" / "factory-kit" / "beeper-token").chmod(0o644)
    out = install(kit, env)
    assert "chmod 600" in out.stdout


def test_generated_files_hold_no_token(tmp_path):
    home, kit, env = setup_install(tmp_path)
    assert install(kit, env, "--apply").returncode == 0
    for p in (kit / "generated").iterdir():
        assert "fake-token-for-tests" not in p.read_text(), p
    for p in (home / ".claude" / "settings.json", home / ".codex" / "hooks.json", home / ".zshrc"):
        assert "fake-token-for-tests" not in p.read_text(), p
