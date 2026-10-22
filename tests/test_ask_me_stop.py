"""ask_me_stop.py (shipped test, generalised): a turn ending with a question is sent back once."""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "ask_me_stop.py"


def run(tmp_path, msg, **extra):
    data = {"hook_event_name": "Stop", "session_id": "s1", "last_assistant_message": msg, **extra}
    env = dict(os.environ, ASK_ME_STATE=str(tmp_path), FACTORY_ENV=str(tmp_path / "none.env"), OWNER_NAME="Alex Example")
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(data), capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def test_blocks_a_turn_that_ends_with_a_question(tmp_path):
    for msg in ["Done with step 1.\n\nShould I also update the docs?",
                "テストは通りました。\n\nこのままpushしてよいですか？",
                "Two options:\n- A\n- B\n\nWhich one do you want?\n",
                "I edited ask-me.py.\n\nShould I also push to their repo?"]:
        out = run(tmp_path, msg)
        assert out and out["decision"] == "block", msg
        assert "ask-me.py" in out["reason"]
        assert "never decide" in out["reason"].lower()  # approvals of external writes are not defaults


def test_lets_finished_or_already_asked_turns_stop(tmp_path):
    for msg in ["All tests pass and it is pushed.",
                "Asked on WhatsApp as Q4; waiting for the reply in the background.",
                "Here is the code:\n```\nif x?:\n```\nDone.",
                "Done. Tests pass. See https://example.com/a?b=1",
                "Draft:\n\n<reply-draft>\nHow was your day?\n</reply-draft>",
                "<reply-draft>Hi</reply-draft>\n<reply-review>\nunderstanding: ok | evidence: she asked \"how was your day?\"\n</reply-review>",
                "<reply-draft-none>she asked what time?</reply-draft-none>",
                "Asked as Q3 with ask-me; working on the rest.",
                "Renamed `is_ok?` to `is_ok`.",
                ""]:
        assert run(tmp_path, msg) is None, msg


def test_never_blocks_twice_in_a_row_and_has_an_off_switch(tmp_path):
    assert run(tmp_path, "Ship it?", stop_hook_active=True) is None
    (tmp_path / "off").write_text("")
    assert run(tmp_path, "Ship it?") is None


def test_reads_the_transcript_when_the_message_field_is_missing(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Proceed?"}]}}) + "\n")
    data = {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(t)}
    env = dict(os.environ, ASK_ME_STATE=str(tmp_path), FACTORY_ENV=str(tmp_path / "none.env"), OWNER_NAME="Alex Example")
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(data), capture_output=True, text=True, env=env)
    assert json.loads(r.stdout)["decision"] == "block"


def test_reason_names_the_owner_and_the_kit_ask_me_and_no_private_rules(tmp_path):
    out = run(tmp_path, "Ship it?")
    reason = out["reason"]
    assert "Alex Example" in reason
    assert str(HOOK.parents[1] / "scripts" / "ask-me.py") in reason
    assert "--timeout 5" in reason and "--wait" in reason  # the Codex way to ask (no background runs)
    for word in ("Case", "mail-send-approval", "cdp-send-checklist"):
        assert word not in reason
