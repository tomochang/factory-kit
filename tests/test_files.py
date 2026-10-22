"""The kit ships every piece of the foreman flow (KPI "pieces shipped", R1, R4, R7, R8)."""
import hashlib
import os
import re

from conftest import KIT

PIECES = [
    "foreman/foreman.sh", "foreman/start-watchdog.sh", "foreman/factory_guard.py", "foreman/codex-run.sh",
    "hooks/guard_bash.py", "NOTICE",
    "foreman/channel/whatsapp-channel.mjs", "foreman/channel/beeper-transport.mjs",
    "foreman/channel/package.json", "foreman/channel/package-lock.json", "foreman/channel/test/channel.test.mjs",
    "watchdog/watchdog.py", "watchdog/watchdog.sha256",
    "scripts/ask-me.py", "scripts/safe-push.sh",
    "hooks/ask_me_stop.py", "hooks/security-bash.sh", "hooks/security-file-write.sh",
    "templates/mcp.json.tmpl", "templates/workspace-settings.json.tmpl", "templates/codex-hooks.json.tmpl",
    "templates/claude-settings.json.tmpl", "templates/channel-instructions.txt.tmpl",
    "templates/factory/CLAUDE.md", "templates/factory/AGENTS.md", "templates/factory/TASKS.md",
    "workflow/WORKFLOW.md", "workflow/WORKFLOW-remote.md", "workflow/check_task.py",
    "lib/kitenv.py", "lib/render.py",
    "install.sh", "doctor.sh", "scan_private.py", "factory.env.example", "README.md",
    "tests/test_clean_install.sh", "tests/run_all.sh",
]
SKILLS = ["imozuru", "foreman", "ask-me", "safe-push"]


def test_every_piece_is_shipped():
    missing = [p for p in PIECES if not (KIT / p).is_file()]
    assert not missing, missing


def test_scripts_are_executable():
    for p in PIECES:
        if p.endswith(".sh") or p in ("scripts/ask-me.py", "scan_private.py"):
            assert os.access(KIT / p, os.X_OK), p


def test_the_watchdog_pin_matches_the_shipped_watchdog():
    want = (KIT / "watchdog" / "watchdog.sha256").read_text().strip()
    assert want == hashlib.sha256((KIT / "watchdog" / "watchdog.py").read_bytes()).hexdigest()


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "no frontmatter"
    keys = re.findall(r"^([A-Za-z_-]+):", m.group(1), re.M)
    name = re.search(r"^name:\s*(\S+)", m.group(1), re.M)
    return keys, name.group(1) if name else None


def test_skills_have_only_name_and_description_so_both_tools_read_them():
    for s in SKILLS:
        text = (KIT / "skills" / s / "SKILL.md").read_text()
        keys, name = frontmatter(text)
        assert keys == ["name", "description"], (s, keys)
        assert name == s


def test_skills_reach_the_kit_scripts_through_their_own_folder():
    for s, script in (("ask-me", "ask-me"), ("safe-push", "safe-push")):
        wrapper = KIT / "skills" / s / "scripts" / script
        assert wrapper.is_file() and os.access(wrapper, os.X_OK), wrapper
        assert "pwd -P" in wrapper.read_text()          # works through the ~/.claude/skills symlink


def test_ask_me_skill_explains_the_codex_way():
    text = (KIT / "skills" / "ask-me" / "SKILL.md").read_text()
    assert "--timeout 5" in text and "--wait" in text and "run_in_background" in text


def test_factory_env_example_has_every_setting_and_is_the_only_env_file_tracked():
    text = (KIT / "factory.env.example").read_text()
    for key in ("OWNER_NAME", "CHAT_ID", "TOKEN_FILE", "BEEPER_BASE", "WORK_DIR", "PROTECTED_DIRS", "TZ",
                "ASK_ME_STATE"):
        assert re.search(rf"^{key}=", text, re.M), key
    ignore = (KIT / ".gitignore").read_text().split()
    assert "factory.env" in ignore and "generated/" in ignore


def test_remote_workflow_variant():
    text = (KIT / "workflow" / "WORKFLOW-remote.md").read_text()
    for word in ("F: go T<n>", "ask-me", "safe-push", "codex-run.sh", "WhatsApp"):
        assert word in text, word
    assert "WORKFLOW-remote.md" in (KIT / "workflow" / "WORKFLOW.md").read_text()


def test_transport_is_one_module():
    ch = (KIT / "foreman" / "channel" / "whatsapp-channel.mjs").read_text()
    assert "beeper-transport.mjs" in ch and "fetch(" not in ch


def test_readme_has_install_steps_for_both_tools():
    text = (KIT / "README.md").read_text()
    for word in ("factory.env.example", "install.sh", "--apply", "--uninstall", "doctor.sh", "/hooks", "Codex",
                 "Claude Code", "foreman.sh", "codex-run.sh", "tests/run_all.sh"):
        assert word in text, word


def test_owner_only_policy_is_not_shipped():
    for p in ("hooks/security-bash.sh", "hooks/ask_me_stop.py", "foreman/channel/whatsapp-channel.mjs"):
        text = (KIT / p).read_text()
        for word in ("act.sh", "MAIL_APPROVED", "cdp-send-checklist", "mail-send-approval", "claude-vps"):
            assert word not in text, (p, word)
