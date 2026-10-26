"""Token meter (SPEC R1, KPI K1/K2): transcript usage de-duplicated by message.id, context = last request total.

The two real transcripts are from the T33 research run (2026-10-26), cut down to row types, ids and usage by
tests/make_run_budget_fixtures.py (no text). Expected numbers: sources/local-2026-10-26-subagent-usage-check.md.
"""
import json
import shutil
import time

import pytest

from rb_helpers import env, make_env, FIX, assistant_row, rb, user_row

REAL = [
    # agent id, context (= notification subagent_tokens), tool uses, cumulative de-duplicated
    ("a0ac34e06a070ed16", 80350, 19, 258260),
    ("ac2e569581df4044a", 98977, 35, 369318),
]
PROBE = [("a7b5cd41c23259814", 34241, 4), ("a7e86692151cf4ef7", 31449, 3), ("a6f4678cb6fd2de44", 33764, 2)]


@pytest.mark.parametrize("aid,ctx,tools,cum", REAL)
def test_real_transcripts(aid, ctx, tools, cum):
    t = rb.transcript_totals(FIX / "transcripts" / ("agent-%s.jsonl" % aid))
    assert t["context_tokens"] == ctx
    assert t["tool_uses"] == tools
    assert t["cumulative"]["total"] == cum
    c = t["cumulative"]
    assert c["input"] + c["output"] + c["cache_creation"] + c["cache_read"] == cum
    assert t["model"] == "claude-haiku-5-5"
    assert t["first_ts"] and t["last_ts"] and t["last_ts"] > t["first_ts"]


def test_searcher_b_breakdown():
    c = rb.transcript_totals(FIX / "transcripts" / "agent-a0ac34e06a070ed16.jsonl")["cumulative"]
    assert (c["input"], c["output"], c["cache_creation"], c["cache_read"]) == (10, 20139, 62141, 175970)
    assert rb.transcript_totals(FIX / "transcripts" / "agent-a0ac34e06a070ed16.jsonl")["requests"] == 5


@pytest.mark.parametrize("aid,ctx,tools", PROBE)
def test_probe_transcripts_match_notifications(aid, ctx, tools):
    """K2 on the probe runs: the notification numbers equal the transcript's last request and tool_use count."""
    t = rb.transcript_totals(FIX / "probe" / ("agent-%s.jsonl" % aid))
    notes = rb.parse_notifications(FIX / "probe" / "main-session.jsonl")
    assert notes[aid]["subagent_tokens"] == ctx == t["context_tokens"]
    assert notes[aid]["tool_uses"] == tools == t["tool_uses"]


def test_real_notifications_parsed_from_parent_transcript():
    notes = rb.parse_notifications(FIX / "transcripts" / "parent-notifications.jsonl")
    assert notes["a0ac34e06a070ed16"]["subagent_tokens"] == 80350
    assert notes["a0ac34e06a070ed16"]["tool_uses"] == 19
    assert notes["ac2e569581df4044a"]["subagent_tokens"] == 98977
    assert notes["ac2e569581df4044a"]["tool_uses"] == 35


def test_notification_turn_limit_and_status():
    notes = rb.parse_notifications(FIX / "probe" / "main-session.jsonl")
    assert "turn limit" in notes["a6f4678cb6fd2de44"]["summary"]
    assert notes["a7b5cd41c23259814"]["status"] == "completed"


def test_incremental_read_equals_full_read(tmp_path):
    src = FIX / "transcripts" / "agent-ac2e569581df4044a.jsonl"
    lines = src.read_text().splitlines(keepends=True)
    p = tmp_path / "agent-x.jsonl"
    p.write_text("".join(lines[:40]))
    meter = {}
    rb.meter_update(meter, p)
    first = rb.meter_summary(meter)
    with p.open("a") as f:
        f.write("".join(lines[40:]))
    rb.meter_update(meter, p)
    full = rb.transcript_totals(src)
    assert rb.meter_summary(meter) == {k: full[k] for k in rb.meter_summary(meter)}
    assert first["cumulative"]["total"] < full["cumulative"]["total"]


def test_partial_last_line_is_read_later(tmp_path):
    """A row being written (no newline yet) is not consumed; the next read picks it up whole."""
    p = tmp_path / "agent-x.jsonl"
    row = json.dumps(assistant_row("m1", "2026-10-26T01:00:00Z", ctx=5000, tools=1))
    p.write_text(row[:30])
    meter = {}
    rb.meter_update(meter, p)
    assert rb.meter_summary(meter)["context_tokens"] == "unknown"
    p.write_text(row + "\n")
    rb.meter_update(meter, p)
    assert rb.meter_summary(meter)["context_tokens"] == 5000


def test_duplicate_rows_counted_once(tmp_path):
    rows = [user_row("2026-10-26T01:00:00Z")]
    r = assistant_row("m1", "2026-10-26T01:00:01Z", ctx=1000, tools=1)
    rows += [r, r, r]
    rows.append(assistant_row("m2", "2026-10-26T01:00:02Z", ctx=2000, tools=0))
    p = tmp_path / "t.jsonl"
    p.write_text("".join(json.dumps(x) + "\n" for x in rows))
    t = rb.transcript_totals(p)
    assert t["cumulative"]["total"] == 3000
    assert t["context_tokens"] == 2000
    assert t["requests"] == 2
    assert t["tool_uses"] == 1  # the same tool_use id in three rows


def test_garbage_and_missing_give_unknown_not_zero(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("not json\n{\"type\": \"assistant\"}\n\n")
    t = rb.transcript_totals(p)
    assert t["context_tokens"] == "unknown"
    assert t["cumulative"]["total"] == "unknown"
    t = rb.transcript_totals(tmp_path / "missing.jsonl")
    assert t["context_tokens"] == "unknown"


def test_find_transcript_agent_and_workflow(tmp_path):
    main = tmp_path / "proj" / "sess.jsonl"
    sub = tmp_path / "proj" / "sess" / "subagents"
    (sub / "workflows" / "wf_1").mkdir(parents=True)
    main.write_text("")
    a = sub / "agent-aaa.jsonl"
    a.write_text("")
    w = sub / "workflows" / "wf_1" / "agent-www.jsonl"
    w.write_text("")
    assert rb.find_transcript(str(main), "aaa") == a
    assert rb.find_transcript(str(main), "www") == w
    assert rb.find_transcript(str(main), "nope") is None
    assert rb.find_transcript("", "aaa") is None


def test_hook_is_fast_on_a_300k_token_transcript(env):
    """R12: under 100 ms per call on a 300k-token transcript, because it reads only what was appended."""
    rows = [user_row("2026-10-26T01:00:00Z", "x" * 2000)]
    for i in range(400):  # ~4 MB, context climbing to 300k
        rows.append(assistant_row("m%d" % i, "2026-10-26T01:%02d:%02dZ" % (i // 60 % 60, i % 60),
                                  ctx=1000 + i * 750, tools=1))
        rows.append({"type": "user", "timestamp": "2026-10-26T01:00:00Z",
                     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_m%d_0" % i,
                                                              "content": "y" * 8000}]}})
    env.transcript("big", rows=rows)
    env.now = rb.parse_ts("2026-10-26T01:10:00Z")
    env.pre("big", "Bash", {"command": "echo 1"})  # first call reads the whole file
    times = []
    for i in range(5):
        with (env.subdir / "agent-big.jsonl").open("a") as f:
            f.write(json.dumps(assistant_row("n%d" % i, "2026-10-26T01:10:00Z", ctx=300000, tools=1)) + "\n")
        t = time.perf_counter()
        env.pre("big", "Bash", {"command": "echo %d" % i})
        times.append(time.perf_counter() - t)
    assert max(times) < 0.1, times
