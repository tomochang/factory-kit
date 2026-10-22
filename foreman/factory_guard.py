#!/usr/bin/env python3
"""PreToolUse hook for factory sessions (Claude Code and Codex): never change a protected folder.

Why: a foreman once started inside the owner's main repo made a commit there that swept up other sessions'
staged files and then moved HEAD back with a reset. The foreman now runs in WORK_DIR, and this guard refuses
git writes whose working repo is a protected folder (cwd, `cd` in the same command, or `git -C`) and edits of
files in one. Reading those folders and running their scripts stays allowed.

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
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import kitenv  # noqa: E402

READ_ONLY = {"status", "log", "diff", "show", "rev-parse", "ls-files", "cherry", "merge-base", "blame",
             "grep", "describe"}  # branch/remote/config/worktree/fetch can write, so they count as writes
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

    def git_write(self, command, cwd):
        where = cwd
        for seg in re.split(r"&&|\|\||;|\||\n", command):
            try:
                words = shlex.split(seg)
            except ValueError:
                words = seg.split()
            if not words:
                continue
            if words[0] == "cd" and len(words) > 1:
                where = os.path.join(where, os.path.expanduser(os.path.expandvars(words[1])))
                continue
            if words[0] != "git":
                continue
            repo, rest = where, words[1:]
            while rest and rest[0].startswith("-"):
                if rest[0] == "-C" and len(rest) > 1:
                    repo = os.path.join(repo, os.path.expanduser(os.path.expandvars(rest[1])))
                    rest = rest[2:]
                else:
                    rest = rest[1:]
            sub = rest[0] if rest else ""
            if sub and sub not in READ_ONLY and self.inside_protected(real(repo)):
                return f"git {sub}"
        return None

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
        if isinstance(raw, list):  # Codex argv form, e.g. ["bash", "-lc", "<script>"]: check the script too
            parts = [" ".join(shlex.quote(str(w)) for w in raw)] + [str(w) for w in raw[1:]]
        else:
            parts = [raw] if isinstance(raw, str) else []
        text = "\n".join(parts)
        if tool == "apply_patch" or "*** Begin Patch" in text:
            hit = self.patch_hit(text, cwd)
            if hit:
                return (f"the factory must not edit files in a protected folder ({hit}); "
                        "work in the task's own repo or worktree")
        if tool == "Bash":
            for part in parts:
                hit = self.git_write(part, cwd)
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
