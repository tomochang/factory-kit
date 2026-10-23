"""T22: the WhatsApp reply guard (hooks/reply_guard.py), PreToolUse on mcp__whatsapp__reply and Stop.

KPIs (docs/t22/KGI.md): 0 replies delivered that are too long, start without saying what the owner must do,
point at a local file path, claim done/green for a PR whose checks are not all green, or come as a burst; 0 turns
that end with an owner message unanswered. Every rule is fed a bad reply here and must deny it with a rewrite hint.
After delivery the KGI is measured on the live log: <ASK_ME_STATE>/channel/replies.jsonl (weekly scan, same rules).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import KIT

HOOK = KIT / "hooks" / "reply_guard.py"
TOOL = "mcp__whatsapp__reply"
GOOD = "You: reply F: go T5 to start the build.\nT5 spec: word counter CLI, tested with pytest."

FAKE_GH = r'''#!/usr/bin/env python3
# fake gh: answers `gh pr checks N --repo R --json ...` from $FAKE_GH_DIR/<owner>_<repo>_<N>.json
import json, os, sys, time
d = os.environ["FAKE_GH_DIR"]
with open(os.path.join(d, "calls.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
a = sys.argv[1:]
if a[:2] != ["pr", "checks"] or "--repo" not in a:
    sys.exit(2)
n, repo = a[2], a[a.index("--repo") + 1]
base = os.path.join(d, repo.replace("/", "_") + "_" + n)
if os.path.exists(base + ".sleep"):
    time.sleep(30)
if not os.path.exists(base + ".json"):
    sys.stderr.write("GraphQL: Could not resolve to a Repository\n")
    sys.exit(1)
data = json.load(open(base + ".json"))
if not data:
    sys.stderr.write("no checks reported on the 'x' branch\n")
    sys.exit(1)
print(json.dumps(data))
pending = any(c["bucket"] == "pending" for c in data)
failed = any(c["bucket"] in ("fail", "cancel") for c in data)
sys.exit(8 if pending else 1 if failed else 0)
'''


class Env:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.state = tmp_path / "state"
        self.chan = self.state / "channel"
        (self.chan / "inbox").mkdir(parents=True)
        self.gh_dir = tmp_path / "gh"
        self.gh_dir.mkdir()
        bindir = tmp_path / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(FAKE_GH.replace("/usr/bin/env python3", sys.executable, 1))
        gh.chmod(0o755)
        self.env_file = tmp_path / "factory.env"
        self.settings = {"OWNER_NAME": "Alex Example", "ASK_ME_STATE": str(self.state), "REPLY_GH_OWNER": "acme"}
        self.base = {k: v for k, v in os.environ.items()
                     if not k.startswith(("REPLY_", "FACTORY_", "ASK_ME_", "BEEPER_"))
                     and k not in ("OWNER_NAME", "CHAT_ID", "TOKEN_FILE", "WORK_DIR", "PROTECTED_DIRS")}
        self.base.update(PATH=f"{bindir}:{os.environ['PATH']}", HOME=str(tmp_path), FAKE_GH_DIR=str(self.gh_dir),
                         FACTORY_ENV=str(self.env_file))

    def run(self, event, **extra_env):
        self.env_file.write_text("".join(f'{k}="{v}"\n' for k, v in self.settings.items()))
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True,
                           env={**self.base, **extra_env}, timeout=60)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout) if r.stdout.strip() else None

    def reply(self, text, **extra_env):
        out = self.run({"hook_event_name": "PreToolUse", "tool_name": TOOL, "tool_input": {"text": text},
                        "cwd": str(self.tmp)}, **extra_env)
        if out is None:
            return None
        h = out["hookSpecificOutput"]
        assert h["permissionDecision"] == "deny"
        return h["permissionDecisionReason"]

    def stop(self, session="1", **kw):
        env = {"FACTORY_SESSION": session} if session else {}
        out = self.run({"hook_event_name": "Stop", "stop_hook_active": kw.get("active", False),
                        "session_id": "s", "transcript_path": str(self.tmp / "t.jsonl")}, **env)
        return out

    def checks(self, repo, n, buckets, sleep=False):
        base = self.gh_dir / f"{repo.replace('/', '_')}_{n}"
        (base.with_suffix(".json")).write_text(json.dumps([{"name": f"c{i}", "state": b.upper(), "bucket": b}
                                                           for i, b in enumerate(buckets)]))
        if sleep:
            base.with_suffix(".sleep").write_text("")

    def gh_calls(self):
        f = self.gh_dir / "calls.log"
        return f.read_text().splitlines() if f.exists() else []

    def inbox(self, mid, received_at=None, **fields):
        data = {"id": mid, "text": "F: build it", "received_at": received_at or time.time(), "answered_at": None,
                "stale": False, "stop_blocks": 0, "receipt": "reaction"}
        data.update(fields)
        (self.chan / "inbox" / f"{mid}.json").write_text(json.dumps(data))

    def read_inbox(self, mid):
        return json.loads((self.chan / "inbox" / f"{mid}.json").read_text())

    def sent(self, t, text="You: x"):
        with open(self.chan / "replies.jsonl", "a") as f:
            f.write(json.dumps({"t": t, "text": text}) + "\n")


@pytest.fixture
def g(tmp_path):
    return Env(tmp_path)


# --- R2: length and lines

def test_a_short_reply_that_says_what_he_must_do_passes(g):
    assert g.reply(GOOD) is None


def test_other_tools_are_not_checked(g):
    assert g.run({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}) is None


def test_over_700_characters_is_refused_with_how_to_rewrite(g):
    why = g.reply("You: read this.\n" + "x" * 700)
    assert "700" in why and "characters" in why
    assert "You:" in why  # the rewrite shape is spelled out
    assert "not sent" in why


def test_over_10_lines_is_refused(g):
    why = g.reply("You: nothing to read.\n" + "\n".join(f"line {i}" for i in range(10)))
    assert "10 lines" in why


def test_limits_come_from_factory_env(g):
    g.settings.update(REPLY_MAX_CHARS="60", REPLY_MAX_LINES="2")
    assert "60" in g.reply("Nothing needed - " + "y" * 60)
    assert "2 lines" in g.reply("Nothing needed.\na\nb")
    assert g.reply("Nothing needed.\nT5 done.") is None


# --- R2: first line

@pytest.mark.parametrize("first", ["You: approve the PR on the Mac", "Nothing needed - T5 is running",
                                   "Got it - T5 received, researching", "you: pick A or B"])
def test_first_line_says_what_he_must_do(g, first):
    assert g.reply(first + "\nmore detail") is None


@pytest.mark.parametrize("first", ["Here is the update on T5", "T5 done!", "Sorry for the delay", "1/3 report"])
def test_first_line_without_his_action_is_refused(g, first):
    why = g.reply(first + "\nmore")
    assert "first line" in why.lower()
    assert '"You:' in why and "Nothing needed" in why and "Got it" in why


# --- R2: local file paths

@pytest.mark.parametrize("path", ["~/factory/whatsapp-style/SPEC.md", "/Users/x/factory/t5/SPEC.md",
                                  "/private/tmp/t5-kit/README.md", "/home/x/notes.md", "/tmp/out.log"])
def test_a_local_path_in_the_body_is_refused(g, path):
    why = g.reply(f"Nothing needed - the spec is in {path}")
    assert "path" in why.lower() and "File:" in why
    assert "phone" in why


def test_a_path_on_a_last_file_line_is_allowed(g):
    assert g.reply("Nothing needed - spec: GET /health returns ok, tested with curl.\n"
                   "File: ~/factory/health/SPEC.md") is None


def test_a_file_line_that_is_not_last_does_not_excuse_the_path(g):
    assert g.reply("Nothing needed.\nFile: ~/factory/a.md\nmore text") is not None


def test_urls_and_api_routes_are_not_local_paths(g):
    assert g.reply("Nothing needed - GET /health and /v1/users work; see https://example.com/Users/x/tmp/") is None


# --- R2: done/green claims are checked against the PR's checks

URL = "https://github.com/acme/kit/pull/7"


def test_green_claim_with_all_checks_passing_is_allowed(g):
    g.checks("acme/kit", 7, ["pass", "pass", "skipping"])
    assert g.reply(f"You: merge {URL}\nCI green, all checks passed.") is None
    assert any(c.startswith("pr checks 7 --repo acme/kit") for c in g.gh_calls())


@pytest.mark.parametrize("buckets", [["pass", "pending"], ["pass", "fail"], ["cancel"]])
def test_done_claim_with_checks_not_all_green_is_refused(g, buckets):
    g.checks("acme/kit", 7, buckets)
    why = g.reply(f"Nothing needed - T7 done, PR ready: {URL}")
    assert "acme/kit#7" in why
    assert "gh pr checks" in why


def test_a_pr_with_no_checks_cannot_be_called_green(g):
    g.checks("acme/kit", 7, [])
    assert "no checks" in g.reply(f"You: merge {URL}, CI green")
    assert g.reply(f"You: merge {URL} - ready, no CI checks on this PR") is None


def test_gh_timeout_blocks_the_claim(g):
    g.settings["REPLY_GH_TIMEOUT"] = "1"
    g.checks("acme/kit", 7, ["pass"], sleep=True)
    t = time.time()
    why = g.reply(f"Nothing needed - merged {URL}")
    assert time.time() - t < 15
    assert "could not" in why.lower() and "not verified" in why


def test_repo_name_plus_pr_number_uses_the_configured_owner(g):
    g.checks("acme/factory-kit", 4, ["pass"])
    assert g.reply("Nothing needed - factory-kit PR #4 is green") is None
    g.checks("other/tool", 9, ["pending"])
    assert "other/tool#9" in g.reply("Nothing needed - other/tool#9 done")


def test_a_pr_number_without_a_repo_is_refused_until_it_is_named(g):
    why = g.reply("Nothing needed - PR #4 ready, CI green")
    assert "full PR URL" in why
    assert g.gh_calls() == []


def test_a_ci_claim_with_no_pr_at_all_is_refused(g):
    assert "full PR URL" in g.reply("Nothing needed - CI is green, all done")


def test_negated_claims_and_test_counts_need_no_check(g):
    g.checks("acme/kit", 7, ["pending"])
    assert g.reply(f"Nothing needed - {URL}: CI not green yet, I am watching it.") is None
    assert g.reply("Nothing needed - T5 built, 40 tests passed locally.") is None
    assert g.gh_calls() == []


# --- R2: bursts

def test_third_reply_within_two_minutes_without_a_new_message_is_refused(g):
    now = time.time()
    g.inbox("10", received_at=now - 300, answered_at=now - 200)
    g.sent(now - 60)
    g.sent(now - 30)
    why = g.reply(GOOD)
    assert "3rd" in why and "one message" in why


def test_a_new_message_from_him_resets_the_burst(g):
    now = time.time()
    g.sent(now - 60)
    g.sent(now - 30)
    g.inbox("11", received_at=now - 10)
    assert g.reply(GOOD) is None


def test_replies_older_than_the_window_do_not_count(g):
    now = time.time()
    g.sent(now - 400)
    g.sent(now - 300)
    g.sent(now - 30)
    assert g.reply(GOOD) is None


def test_burst_limits_come_from_factory_env(g):
    g.settings.update(REPLY_BURST_MAX="1", REPLY_BURST_SECONDS="600")
    g.sent(time.time() - 500)
    assert "2nd" in g.reply(GOOD)


def test_every_problem_is_listed_at_once(g):
    why = g.reply("Here you go\nsee ~/factory/x/SPEC.md\n" + "z" * 800)
    assert "first line" in why.lower() and "path" in why.lower() and "700" in why


# --- R3: unanswered guard (Stop)

def test_stop_is_blocked_while_his_message_has_no_reply(g):
    g.inbox("20", text="F: why is CI red?")
    out = g.stop()
    assert out["decision"] == "block"
    assert "why is CI red?" in out["reason"]
    assert "reply" in out["reason"] and "Got it" in out["reason"]
    assert g.read_inbox("20")["stop_blocks"] == 1


def test_only_the_foreman_session_is_held(g):
    g.inbox("21")
    assert g.stop(session="") is None


def test_stop_passes_after_the_reply(g):
    now = time.time()
    g.inbox("22", received_at=now - 50, answered_at=now - 5)
    assert g.stop() is None


def test_a_logged_reply_after_the_message_counts_even_if_the_mark_was_missed(g):
    now = time.time()
    g.inbox("23", received_at=now - 50)
    g.sent(now - 10)
    assert g.stop() is None


def test_a_reply_before_the_message_does_not_count(g):
    now = time.time()
    g.sent(now - 100)
    g.inbox("24", received_at=now - 50)
    assert g.stop()["decision"] == "block"


def test_stale_messages_from_an_earlier_run_are_ignored(g):
    g.inbox("25", stale=True)
    assert g.stop() is None


def test_it_gives_up_after_three_blocks_per_message(g):
    g.inbox("26")
    for _ in range(3):
        assert g.stop(active=True)["decision"] == "block"
    assert g.stop(active=True) is None
    assert g.read_inbox("26")["stop_blocks"] == 3


def test_no_state_folder_means_nothing_to_hold(g, tmp_path):
    g.settings["ASK_ME_STATE"] = str(tmp_path / "nowhere")
    assert g.stop() is None
    assert g.reply(GOOD) is None
