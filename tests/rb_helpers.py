"""Shared helpers for the run-budget tests (T33). Nothing here reads or writes the real ~/factory/.runs or ~/.claude.

The same test files run in three layouts: the run-budget repo (run_budget.py at the top, tests/), factory-kit
(hooks/run_budget.py, scripts/run-budget, tests/) and clawd (projects/factory/ holds code and tests side by side).
The test modules import their fixtures (env, make_env) from here, so no conftest.py is needed.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _find(*cands):
    for c in cands:
        if c.exists():
            return c
    return cands[0]


CODE = _find(HERE.parent / "run_budget.py", HERE.parent / "hooks" / "run_budget.py", HERE / "run_budget.py")
CLI = _find(CODE.parent / "run-budget", HERE.parent / "scripts" / "run-budget")
FIX = HERE / "fixtures" / "run_budget"
AGENTS = _find(HERE.parent / "agents", HERE.parent / "templates" / "factory" / ".claude" / "agents", HERE / "agents")
SETTINGS = _find(HERE.parent / "settings-hooks.json", HERE.parent / "templates" / "workspace-settings.json.tmpl",
                 HERE / "workspace-settings.json")
CONF = _find(CODE.parent / "run_budget.conf", HERE.parent / "factory.env.example")
sys.path.insert(0, str(CODE.parent))

import run_budget as rb  # noqa: E402

T0 = 1793000000.0  # a fixed "now" for the tests (2026-10-26)


class Env:
    """One fake foreman session: runs dir, a projects dir with <session>.jsonl and subagents/, and the config."""

    def __init__(self, tmp, monkeypatch, **overrides):
        self.tmp = Path(tmp)
        self.runs = self.tmp / "runs"
        self.proj = self.tmp / "projects" / "-Users-you-factory"
        self.session = "s-0001"
        self.main = self.proj / (self.session + ".jsonl")
        self.subdir = self.proj / self.session / "subagents"
        self.subdir.mkdir(parents=True)
        self.main.write_text("")
        for k in list(__import__("os").environ):
            if k.startswith("RB_") or k in ("RUN_BUDGET_DIR", "RUN_BUDGET_CONF"):
                monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("RUN_BUDGET_DIR", str(self.runs))
        monkeypatch.setenv("RUN_BUDGET_CONF", str(self.tmp / "none.conf"))
        monkeypatch.setenv("RB_STOP_POLL_S", "0")
        for k, v in overrides.items():
            monkeypatch.setenv(k, str(v))
        self.now = T0

    def cfg(self):
        return rb.load_config()

    def transcript(self, agent_id, fixture=None, rows=None):
        p = self.subdir / ("agent-%s.jsonl" % agent_id)
        if fixture:
            shutil.copyfile(fixture, p)
        else:
            p.write_text("".join(json.dumps(r) + "\n" for r in (rows or [])))
        return p

    def meta(self, agent_id, **fields):
        d = {"agentType": "factory-builder", "description": "T33 build", "requestShape": "background"}
        d.update(fields)
        (self.subdir / ("agent-%s.meta.json" % agent_id)).write_text(json.dumps(d))

    def event(self, name, agent_id=None, agent_type="factory-builder", **kw):
        ev = {"session_id": self.session, "transcript_path": str(self.main), "cwd": str(self.tmp),
              "hook_event_name": name}
        if agent_id:
            ev["agent_id"] = agent_id
            ev["agent_type"] = agent_type
        ev.update(kw)
        return ev

    def call(self, ev, dt=1.0):
        """Run one hook event at the current fake time, then advance the clock by dt seconds."""
        out = rb.handle(ev, self.cfg(), now=self.now)
        self.now += dt
        return out

    def pre(self, aid, tool="Bash", tool_input=None, **kw):
        return self.call(self.event("PreToolUse", aid, tool_name=tool, tool_input=tool_input or {}, **kw))

    def post(self, aid, tool="Bash", tool_input=None, response=None, **kw):
        return self.call(self.event("PostToolUse", aid, tool_name=tool, tool_input=tool_input or {},
                                    tool_response=response if response is not None else {"stdout": "ok"}, **kw))

    def fail(self, aid, tool="Bash", tool_input=None, error="Exit code 1\nboom", **kw):
        return self.call(self.event("PostToolUseFailure", aid, tool_name=tool, tool_input=tool_input or {},
                                    error=error, **kw))

    def bash(self, aid, cmd, stdout="ok", fail=None):
        """Pre + (Post or Failure) for one Bash call; returns the PreToolUse output."""
        inp = {"command": cmd}
        out = self.pre(aid, "Bash", inp)
        if decision(out) == "deny":
            return out
        if fail is not None:
            self.fail(aid, "Bash", inp, error=fail)
        else:
            self.post(aid, "Bash", inp, {"stdout": stdout, "stderr": "", "interrupted": False})
        return out

    def stop(self, aid, agent_type="factory-builder"):
        return self.call(self.event("SubagentStop", aid, agent_type=agent_type,
                                    agent_transcript_path=str(self.subdir / ("agent-%s.jsonl" % aid))))

    def rows(self):
        p = self.runs / "runs.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def decision(out):
    return ((out or {}).get("hookSpecificOutput") or {}).get("permissionDecision")


def reason(out):
    return ((out or {}).get("hookSpecificOutput") or {}).get("permissionDecisionReason") or ""


def context(out):
    return ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext") or ""


def assistant_row(msg_id, ts, ctx=1000, out_tokens=10, tools=0, model="claude-haiku-5-5"):
    """One synthetic assistant transcript row whose last-request total is ctx."""
    usage = {"input_tokens": 1, "output_tokens": out_tokens, "cache_creation_input_tokens": 0,
             "cache_read_input_tokens": max(0, ctx - 1 - out_tokens)}
    content = [{"type": "tool_use", "id": "toolu_%s_%d" % (msg_id, i), "name": "Bash", "input": {}}
               for i in range(tools)]
    return {"type": "assistant", "timestamp": ts, "message": {"id": msg_id, "model": model, "role": "assistant",
                                                              "usage": usage, "content": content}}


def user_row(ts, text="go"):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


@pytest.fixture
def make_env(tmp_path, monkeypatch):
    def make(**overrides):
        return Env(tmp_path, monkeypatch, **overrides)
    return make


def git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    return path


def start_from_agent_call(env, aid, description):
    """The foreman's PostToolUse on Agent maps the returned agentId to its description (T number)."""
    env.call(env.event("PostToolUse", None, tool_name="Agent",
                       tool_input={"description": description, "subagent_type": "factory-builder", "prompt": "x"},
                       tool_response={"isAsync": True, "status": "async_launched", "agentId": aid,
                                      "description": description}))
