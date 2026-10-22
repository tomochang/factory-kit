"""R5 (T15): build agents run in the OS sandbox; the interactive foreman does not.

- Claude builds: foreman/claude-run.sh starts `claude -p --settings <generated/build-settings.json>` in the
  worktree. The settings turn the sandbox on (writes: the worktree = cwd, plus npm/pip caches), deny reads of
  the ssh/aws/gh/gnupg folders, the Beeper token file and factory.env, forbid the unsandboxed retry, and run
  git (worktree .git lives outside the worktree, issue #46000), safe-push and ask-me outside the sandbox.
  Passing it with --settings makes the sandbox admin-required, so repo settings can't loosen it.
- Codex builds: codex-run.sh passes `--sandbox workspace-write`.
- The same two Bash hooks guard both (R6).
Uses fake claude/codex binaries; nothing real is started.
"""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from guardlib import KIT

PARTS = ("hooks", "foreman", "lib", "templates")


def fake_bins(d):
    d.mkdir(parents=True, exist_ok=True)
    for name in ("claude", "codex"):
        p = d / name
        p.write_text("#!/bin/sh\n"
                     f'{{ echo "{name} cwd=$(pwd -P) session=${{FACTORY_SESSION:-}}"; for a in "$@"; do echo "arg=$a"; done; }}'
                     f' >> "{d}/calls.log"\n'
                     f'if [ ! -t 0 ]; then cat > "{d}/{name}.stdin"; fi\n')
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return d


@pytest.fixture
def kit(tmp_path):
    k = tmp_path / "kit"
    for part in PARTS:
        shutil.copytree(KIT / part, k / part, ignore=shutil.ignore_patterns("__pycache__"))
    home = tmp_path / "home"
    (home / ".config" / "factory-kit").mkdir(parents=True)
    token = home / ".config" / "factory-kit" / "beeper-token"
    token.write_text("fake\n")
    (k / "factory.env").write_text(
        'OWNER_NAME="Alex Example"\nCHAT_ID="!self:beeper.local"\n'
        'TOKEN_FILE="$HOME/.config/factory-kit/beeper-token"\nBEEPER_BASE="http://127.0.0.1:23373"\n'
        'WORK_DIR="$HOME/factory"\nPROTECTED_DIRS="$HOME/private-repo"\n')
    bins = fake_bins(tmp_path / "bin")
    env = {k2: v for k2, v in os.environ.items()
           if not k2.startswith(("FACTORY_", "BEEPER_", "ASK_ME_")) and k2 not in (
               "PROTECTED_DIRS", "WORK_DIR", "TOKEN_FILE", "CHAT_ID", "OWNER_NAME")}
    env.update(HOME=str(home), PATH=f"{bins}:{os.environ['PATH']}")
    r = subprocess.run(["python3", str(k / "lib" / "render.py")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return k.resolve(), home, env, bins


def gen(k, name):
    return json.loads((k / "generated" / name).read_text())


def test_build_settings_turn_the_sandbox_on_and_strict(kit):
    k, home, env, _ = kit
    s = gen(k, "build-settings.json")["sandbox"]
    assert s["enabled"] is True
    assert s["allowUnsandboxedCommands"] is False
    assert s["failIfUnavailable"] is True
    assert s["autoAllowBashIfSandboxed"] is True


def test_build_sandbox_writes_are_the_worktree_plus_caches_only(kit):
    k, home, env, _ = kit
    allow = gen(k, "build-settings.json")["sandbox"]["filesystem"]["allowWrite"]
    assert "~/.npm" in allow and any("pip" in p for p in allow)
    for broad in ("~", "~/", "/", str(home), f"{home}/factory", "/private/tmp", "/tmp", "."):
        assert broad not in allow, broad
    assert all("cache" in p.lower() or p == "~/.npm" for p in allow), allow


def test_build_sandbox_denies_reading_credentials(kit):
    k, home, env, _ = kit
    deny = gen(k, "build-settings.json")["sandbox"]["filesystem"]["denyRead"]
    for p in ("~/.ssh", "~/.aws", "~/.config/gh", "~/.gnupg", str(home / ".config" / "factory-kit" / "beeper-token"),
              str(k / "factory.env")):
        assert p in deny, p


def test_git_and_the_kit_scripts_run_outside_the_sandbox_and_stay_guarded(kit):
    k, home, env, _ = kit
    b = gen(k, "build-settings.json")
    ex = b["sandbox"]["excludedCommands"]
    assert "git *" in ex
    assert f"{k}/scripts/safe-push.sh *" in ex and f"{k}/scripts/ask-me.py *" in ex
    assert "127.0.0.1:23373" in b["sandbox"]["network"]["allowedDomains"]
    ws = gen(k, "workspace-settings.json")
    assert b["hooks"] == ws["hooks"]  # same guards as the foreman's workspace


def test_the_interactive_foreman_is_not_sandboxed(kit):
    k, home, env, _ = kit
    assert "sandbox" not in gen(k, "workspace-settings.json")


def calls(bins):
    p = bins / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


def test_claude_run_starts_a_sandboxed_build_in_the_worktree(kit, tmp_path):
    k, home, env, bins = kit
    wt = tmp_path / "t1-wt"
    wt.mkdir()
    task = tmp_path / "t1.md"
    task.write_text("Build it.\n")
    r = subprocess.run(["bash", str(k / "foreman" / "claude-run.sh"), str(wt), str(task), "--model", "opus"],
                       env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    log = calls(bins)
    assert log[0] == f"claude cwd={wt.resolve()} session=1"
    args = [l[4:] for l in log if l.startswith("arg=")]
    assert args == ["-p", "--settings", str(k / "generated" / "build-settings.json"),
                    "--permission-mode", "acceptEdits", "--model", "opus"]
    assert (bins / "claude.stdin").read_text() == "Build it.\n"
    assert "command claude" in (k / "foreman" / "claude-run.sh").read_text()


def test_claude_run_refuses_protected_folders_and_missing_settings(kit, tmp_path):
    k, home, env, bins = kit
    task = tmp_path / "t.md"
    task.write_text("x")
    prot = home / "private-repo"
    prot.mkdir()
    r = subprocess.run(["bash", str(k / "foreman" / "claude-run.sh"), str(prot), str(task)], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 2 and "protected" in r.stderr
    wt = tmp_path / "wt"
    wt.mkdir()
    (k / "generated" / "build-settings.json").unlink()
    r = subprocess.run(["bash", str(k / "foreman" / "claude-run.sh"), str(wt), str(task)], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 2 and "install.sh --apply" in r.stderr
    assert calls(bins) == []


def test_codex_run_uses_the_workspace_write_sandbox(kit, tmp_path):
    k, home, env, bins = kit
    wt = tmp_path / "t2-wt"
    wt.mkdir()
    task = tmp_path / "t2.md"
    task.write_text("Build it.\n")
    r = subprocess.run(["bash", str(k / "foreman" / "codex-run.sh"), str(wt), str(task), "--model", "gpt-5.5"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    args = [l[4:] for l in calls(bins) if l.startswith("arg=")]
    assert args == ["exec", "--cd", str(wt.resolve()), "--sandbox", "workspace-write", "--model", "gpt-5.5", "-"]


def test_codex_and_claude_use_the_same_bash_hooks(kit):
    k, home, env, _ = kit
    ws = gen(k, "workspace-settings.json")["hooks"]["PreToolUse"]
    cx = gen(k, "codex-hooks.json")["hooks"]["PreToolUse"]
    bash_ws = next(h["hooks"] for h in ws if h["matcher"] == "Bash")
    bash_cx = next(h["hooks"] for h in cx if h["matcher"] == "Bash")
    assert bash_ws == bash_cx
    assert any("security-bash.sh" in h["command"] for h in bash_ws)
    assert any("factory_guard.py" in h["command"] for h in bash_ws)
