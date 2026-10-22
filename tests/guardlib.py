"""Shared helpers for the T15 guard tests. Hooks are run as the agents run them: JSON on stdin, as a subprocess.

Nothing here runs a guarded command: every command is only text handed to a hook.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
TESTS = KIT / "tests"
FIXTURES = TESTS / "fixtures"
SEC_BASH = KIT / "hooks" / "security-bash.sh"
FILE_WRITE = KIT / "hooks" / "security-file-write.sh"
GUARD = KIT / "foreman" / "factory_guard.py"
HOME = os.path.expanduser("~")
WORK = f"{HOME}/factory"


def guard_env(protected, **extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith("FACTORY_")}
    e.update(FACTORY_ENV="/nonexistent/factory.env", PROTECTED_DIRS=protected, WORK_DIR=WORK, FACTORY_SESSION="1")
    e.update(extra)
    return e


def sec_bash(command, cwd=WORK, env=None):
    """security-bash.sh: (blocked, stderr)."""
    ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}
    r = subprocess.run(["bash", str(SEC_BASH)], input=json.dumps(ev), capture_output=True, text=True, env=env)
    assert r.returncode in (0, 2), (r.returncode, r.stderr)
    return r.returncode == 2, r.stderr


def factory_guard(tool, tool_input, protected, cwd=WORK, **extra):
    """factory_guard.py: (denied, reason)."""
    ev = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": cwd}
    r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(ev), capture_output=True, text=True,
                       env=guard_env(protected, **extra))
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return False, ""
    out = json.loads(r.stdout)["hookSpecificOutput"]
    return out["permissionDecision"] == "deny", out.get("permissionDecisionReason", "")


def blocked_by_any(command, protected, cwd=WORK):
    """What the agent sees: the Bash command is refused if either Bash hook refuses it."""
    if sec_bash(command, cwd)[0]:
        return True
    return factory_guard("Bash", {"command": command}, protected, cwd)[0]
