#!/usr/bin/env python3
"""PreToolUse hook for factory sessions (Claude Code and Codex): never change a protected folder.

Why: a foreman once started inside the owner's main repo made a commit there that swept up other sessions'
staged files and then moved HEAD back with a reset. The foreman now runs in WORK_DIR, and this guard refuses
Bash writes whose target is a protected folder - git writes there (cwd, `cd` in the same command, `git -C`) and,
since T15, any other tool: redirections, tee, sed -i, cp/mv/ln/touch/mkdir/rm/chmod, inline python/node writes -
and edits of files in one. The Bash command is parsed by hooks/guard_bash.py. Reading those folders and running their scripts stays allowed.

Protected folders: PROTECTED_DIRS in factory.env (colon-separated) plus the kit itself, so the factory can't
edit its own guards or the watchdog pin.
Tool shapes: Claude Code sends Write/Edit/MultiEdit/NotebookEdit with tool_input.file_path; Codex sends file
edits as tool_name "apply_patch" with the patch text in tool_input.command (paths on "*** Add File:",
"*** Update File:", "*** Delete File:" and "*** Move to:" lines, relative to the session folder), and Bash
commands as a string or an argv list.
Scope: factory sessions only - FACTORY_SESSION=1 (set by foreman.sh and codex-run.sh), a session folder inside
WORK_DIR, or a git worktree of a repo inside WORK_DIR. The Codex hooks are global, so the owner's own sessions
in their repos are left alone.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import guard_bash  # noqa: E402
import kitenv  # noqa: E402

PATCH_PATH = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to):\s*(.+?)\s*$", re.M)
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def real(path, cwd="/"):
    p = os.path.expanduser(os.path.expandvars(path))
    return os.path.realpath(os.path.join(cwd, p))


class Guard:
    def __init__(self, cfg):
        self.protected = [os.path.realpath(p) for p in cfg["PROTECTED_DIRS"]]
        self.work = os.path.realpath(cfg["WORK_DIR"])

    def inside_protected(self, path):
        return any(path == d or path.startswith(d + os.sep) for d in self.protected)

    def factory_session(self, cwd):
        if os.environ.get("FACTORY_SESSION") == "1":
            return True
        c = os.path.realpath(cwd)
        if c == self.work or c.startswith(self.work + os.sep):
            return True
        try:
            r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=c,
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        common = os.path.realpath(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else ""
        return bool(common) and common.startswith(self.work + os.sep)

    def bash_write(self, command, cwd):
        """First reason this Bash command writes into a protected folder (T15 R4: any tool, not only git).

        Uses the guard_bash parser: redirections, tee, sed -i, cp/mv/ln/touch/mkdir/rm/chmod targets, inline
        python/node writes, `cd` earlier in the command, and git writes whose repo is protected."""
        hits = guard_bash.protected_writes(command, os.path.realpath(cwd), self.protected)
        return hits[0] if hits else None

    def patch_hit(self, patch, cwd):
        for m in PATCH_PATH.finditer(patch):
            if self.inside_protected(real(m.group(1), cwd)):
                return m.group(1)
        return None

    def check(self, ev):
        tool, inp = ev.get("tool_name"), ev.get("tool_input") or {}
        cwd = ev.get("cwd") or os.getcwd()
        if not self.factory_session(cwd):
            return None
        raw = inp.get("command") if isinstance(inp, dict) else None
        text = guard_bash.command_text(raw)  # Codex may send an argv list, e.g. ["bash", "-lc", "<script>"]
        if tool == "apply_patch" or "*** Begin Patch" in text:
            hit = self.patch_hit(text, cwd)
            if hit:
                return (f"the factory must not edit files in a protected folder ({hit}); "
                        "work in the task's own repo or worktree")
        if tool == "Bash" and text.strip():
            hit = self.bash_write(text, cwd)
            if hit:
                return (f"the factory must not change a protected folder ({hit}); "
                        "work in the task's own repo or worktree")
        elif tool in FILE_TOOLS:
            path = inp.get("file_path") or inp.get("notebook_path") or ""
            if path and self.inside_protected(real(path, cwd)):
                return "the factory must not edit files in a protected folder; work in the task's own repo or worktree"
        return None


def main():
    try:
        ev = json.load(sys.stdin)
    except ValueError:
        return
    why = Guard(kitenv.load()).check(ev)
    if why:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": "BLOCKED: " + why}}))


if __name__ == "__main__":
    main()
