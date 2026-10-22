"""factory_guard.py (shipped test, generalised): a factory session never changes a protected folder.

The protected folders come from PROTECTED_DIRS in factory.env (here: ~/private-repo). Commands are plain
strings: this file only inspects text, nothing is run in those folders.
"""
import json
import os
import subprocess
import sys

from conftest import KIT

HOOK = KIT / "foreman" / "factory_guard.py"
HOME = os.path.expanduser("~")
PRIV = f"{HOME}/private-repo"
WORK = f"{HOME}/factory"
GIT = "git"


def env(**extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith("FACTORY_")}
    e.update(FACTORY_ENV="/nonexistent/factory.env", PROTECTED_DIRS=PRIV, WORK_DIR=WORK, FACTORY_SESSION="1")
    e.update(extra)
    return e


def denied(tool, tool_input, cwd=WORK, **extra):
    ev = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": cwd}
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(ev), capture_output=True, text=True,
                       env=env(**extra))
    assert r.returncode == 0, r.stderr
    return bool(r.stdout.strip()) and json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def bash(cmd, cwd=WORK, **extra):
    return denied("Bash", {"command": cmd}, cwd, **extra)


def test_blocks_git_writes_that_land_in_a_protected_folder():
    for cmd, cwd in [
        (f"{GIT} commit -m init", PRIV),                                    # the original accident
        (f"{GIT} reset --soft HEAD~1", f"{PRIV}/projects"),
        (f"cd ~/private-repo && {GIT} add -A", WORK),
        (f"cd $HOME/private-repo; {GIT} stash", WORK),
        (f"{GIT} -C ~/private-repo commit -m x", WORK),
        (f"{GIT} -C {PRIV} checkout main", WORK),
        (f"{GIT} -C ~/private-repo worktree add /private/tmp/x", WORK),
        (f"{GIT} branch -D old", PRIV),
    ]:
        assert bash(cmd, cwd), (cmd, cwd)


def test_allows_factory_work_and_reading_protected_folders():
    for cmd, cwd in [
        (f"{GIT} init && {GIT} add hello.py && {GIT} commit -m 'Add CLI'", f"{WORK}/hello"),
        (f"{GIT} commit -m x && {KIT}/scripts/safe-push.sh", "/private/tmp/hello-t1"),
        (f"{KIT}/scripts/ask-me.py --from factory --tell 'T1 done'", WORK),
        (f"{GIT} -C ~/private-repo log --oneline -3", WORK),
        (f"cat {PRIV}/README.md", WORK),
        (f"cd ~/private-repo && {GIT} status", WORK),
    ]:
        assert not bash(cmd, cwd), (cmd, cwd)


def test_blocks_file_edits_inside_protected_folders_only():
    assert denied("Write", {"file_path": f"{PRIV}/scripts/x.py", "content": ""})
    assert denied("Edit", {"file_path": f"{PRIV}/CLAUDE.md", "old_string": "a", "new_string": "b"})
    assert not denied("Write", {"file_path": f"{WORK}/hello/x.py", "content": ""})
    assert not denied("Write", {"file_path": f"{HOME}/private-repo-notes/x.md", "content": ""})  # a sibling


def test_several_protected_folders_and_the_kit_itself():
    other = f"{HOME}/second-private"
    assert denied("Write", {"file_path": f"{other}/a.txt", "content": ""}, PROTECTED_DIRS=f"{PRIV}:{other}")
    assert denied("Write", {"file_path": f"{PRIV}/a.txt", "content": ""}, PROTECTED_DIRS=f"{PRIV}:{other}")
    # the kit's guards and watchdog pin are protected even when PROTECTED_DIRS doesn't list the kit
    assert denied("Write", {"file_path": str(KIT / "hooks" / "security-bash.sh"), "content": ""})
    assert denied("Write", {"file_path": str(KIT / "watchdog" / "watchdog.sha256"), "content": ""})
    assert bash(f"{GIT} -C {KIT} commit -m x")
