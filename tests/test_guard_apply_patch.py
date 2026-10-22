"""Codex file edits arrive as tool_name "apply_patch" with the patch in tool_input.command (Codex hooks doc,
PreToolUse). Without parsing it, the file guards allow every Codex edit (KPI "Codex file guard").
These tests feed the guards the Codex shapes and check Claude Code's file_path shape still works.
"""
import json
import os
import subprocess
import sys

from conftest import KIT

GUARD = KIT / "foreman" / "factory_guard.py"
FILE_WRITE = KIT / "hooks" / "security-file-write.sh"
SEC_BASH = KIT / "hooks" / "security-bash.sh"
HOME = os.path.expanduser("~")
PRIV = f"{HOME}/private-repo"
WORK = f"{HOME}/factory"


def env(**extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith("FACTORY_")}
    e.update(FACTORY_ENV="/nonexistent/factory.env", PROTECTED_DIRS=PRIV, WORK_DIR=WORK, FACTORY_SESSION="1")
    e.update(extra)
    return e


def patch(*ops):
    body = []
    for op, path, *lines in ops:
        body.append(f"*** {op}: {path}")
        body += list(lines)
    return "*** Begin Patch\n" + "\n".join(body) + "\n*** End Patch\n"


def guard(tool, tool_input, cwd=WORK, **extra):
    ev = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": cwd,
          "turn_id": "t1", "tool_use_id": "u1"}
    r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(ev), capture_output=True, text=True,
                       env=env(**extra))
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    return out["permissionDecision"], out["permissionDecisionReason"]


def denied(*a, **k):
    res = guard(*a, **k)
    return bool(res) and res[0] == "deny"


def ap(p, cwd=WORK, **extra):
    return denied("apply_patch", {"command": p}, cwd, **extra)


def test_codex_patches_touching_a_protected_folder_are_denied():
    assert ap(patch(("Update File", f"{PRIV}/scripts/x.py", "@@", "-a", "+b")))
    assert ap(patch(("Add File", f"{PRIV}/new.md", "+hello")))
    assert ap(patch(("Delete File", f"{PRIV}/old.md")))
    # moving a factory file into the protected folder
    assert ap(patch(("Update File", f"{WORK}/a.py", f"*** Move to: {PRIV}/a.py", "@@", "-a", "+b")))
    # one protected file among several allowed ones
    assert ap(patch(("Add File", f"{WORK}/ok.py", "+x"), ("Update File", f"{PRIV}/x.py", "@@", "-a", "+b")))


def test_relative_patch_paths_are_resolved_against_the_session_folder():
    assert ap(patch(("Update File", "scripts/x.py", "@@", "-a", "+b")), cwd=PRIV)
    assert ap(patch(("Add File", "../private-repo/x.py", "+b")), cwd=WORK)
    assert not ap(patch(("Add File", "hello/x.py", "+b")), cwd=WORK)


def test_patches_inside_the_factory_are_allowed():
    assert not ap(patch(("Add File", f"{WORK}/hello/x.py", "+print(1)"),
                        ("Update File", "/private/tmp/t1-wt/README.md", "@@", "-a", "+b")))


def test_the_deny_reason_names_the_folder_rule():
    decision, reason = guard("apply_patch", {"command": patch(("Add File", f"{PRIV}/n.md", "+x"))})
    assert decision == "deny" and reason.startswith("BLOCKED") and "protected" in reason


def test_a_patch_piped_through_bash_is_denied_too():
    cmd = "apply_patch <<'EOF'\n" + patch(("Add File", f"{PRIV}/y.txt", "+x")) + "EOF\n"
    assert denied("Bash", {"command": cmd})


def test_codex_bash_command_given_as_an_argv_list_is_checked():
    assert denied("Bash", {"command": ["bash", "-lc", f"git -C {PRIV} commit -m x"]})
    assert not denied("Bash", {"command": ["bash", "-lc", "git status"]})


def test_claude_file_path_still_works():
    assert denied("Write", {"file_path": f"{PRIV}/x.py", "content": ""})
    assert denied("MultiEdit", {"file_path": f"{PRIV}/x.py", "edits": []})
    assert denied("NotebookEdit", {"notebook_path": f"{PRIV}/n.ipynb", "new_source": ""})
    assert not denied("Write", {"file_path": f"{WORK}/x.py", "content": ""})


def test_sessions_outside_the_factory_are_not_the_factory_guards_business():
    """Codex hooks are global (~/.codex/hooks.json): the owner's own sessions in their repos keep working."""
    p = patch(("Update File", f"{PRIV}/x.py", "@@", "-a", "+b"))
    assert not ap(p, cwd=PRIV, FACTORY_SESSION="")
    assert not denied("Bash", {"command": "git commit -m x"}, cwd=PRIV, FACTORY_SESSION="")
    # a session started in the factory folder is a factory session even without the marker
    assert ap(p, cwd=f"{WORK}/hello", FACTORY_SESSION="")


def file_write(tool, tool_input):
    ev = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": WORK}
    return subprocess.run(["bash", str(FILE_WRITE)], input=json.dumps(ev), capture_output=True, text=True)


def test_file_write_hook_reads_apply_patch_paths_and_added_lines():
    r = file_write("apply_patch", {"command": patch(("Add File", f"{WORK}/app/.env", "+API=1"))})
    assert r.returncode == 2 and "BLOCKED" in r.stderr
    r = file_write("apply_patch", {"command": patch(("Add File", f"{WORK}/app/server.pem", "+x"))})
    assert r.returncode == 2
    key = "-----BEGIN " + "RSA PRIVATE KEY-----"
    r = file_write("apply_patch", {"command": patch(("Add File", f"{WORK}/app/k.txt", "+" + key))})
    assert r.returncode == 2 and "Private key" in r.stderr
    r = file_write("apply_patch", {"command": patch(("Add File", f"{WORK}/app/.env.example", "+API=")),
                                   })
    assert r.returncode == 0, r.stderr
    r = file_write("apply_patch", {"command": patch(("Update File", f"{WORK}/app/main.py", "@@", "-a", "+b"))})
    assert r.returncode == 0, r.stderr


def test_file_write_hook_still_reads_claude_file_path():
    assert file_write("Write", {"file_path": f"{WORK}/.env", "content": "A=1"}).returncode == 2
    assert file_write("Write", {"file_path": f"{WORK}/a.py", "content": "print(1)"}).returncode == 0


def test_security_bash_reads_codex_argv_commands():
    ev = {"tool_name": "Bash", "tool_input": {"command": ["bash", "-lc", "git push --force origin main"]}}
    r = subprocess.run(["bash", str(SEC_BASH)], input=json.dumps(ev), capture_output=True, text=True)
    assert r.returncode == 2 and "BLOCKED" in r.stderr
