"""KGI test (T15): the research bypass corpus run through the Bash hooks exactly as an agent would hit them.

Corpus: tests/fixtures/bypass-corpus.txt (copy of the T15 research corpus; EXPECT<TAB>command<TAB>origin).
- his 15 probe commands (origin "probe", owner-2026-probe-result.txt): all must be blocked (baseline 5/15)
- BLOCK lines: recall >= 98% (baseline 52/189 = 27%)
- ALLOW lines: none may be blocked (baseline 2/29 wrongly blocked)
Protected-folder lines (section 5) are refused by factory_guard.py, the rest by security-bash.sh; both hooks
run on every Bash call, so a line counts as blocked if either refuses it.
"""
import os
from concurrent.futures import ThreadPoolExecutor

from guardlib import FIXTURES, blocked_by_any

CORPUS = FIXTURES / "bypass-corpus.txt"
RECALL_TARGET = 0.98


def load():
    text = CORPUS.read_text()
    # the corpus names the owner's protected folder; the kit's sanitised copy uses ~/private-repo
    name = "private-repo" if "~/private-repo" in text else "clawd"
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        expect, cmd, origin = line.split("\t")
        if "<<" in cmd:
            cmd = cmd.replace("\\n", "\n")  # the heredoc line writes its newlines as \n
        rows.append((expect, cmd, origin))
    return rows, os.path.expanduser(f"~/{name}")


ROWS, PROTECTED = load()


def results():
    if not hasattr(results, "cache"):
        with ThreadPoolExecutor(max_workers=8) as pool:
            got = list(pool.map(lambda r: blocked_by_any(r[1], PROTECTED), ROWS))
        results.cache = [(e, c, o, g) for (e, c, o), g in zip(ROWS, got)]
    return results.cache


def test_corpus_shape():
    assert sum(1 for e, _, _ in ROWS if e == "BLOCK") == 189
    assert sum(1 for e, _, _ in ROWS if e == "ALLOW") == 29
    assert sum(1 for _, _, o in ROWS if o == "probe") == 15


def test_all_15_owner_probes_are_blocked():
    missed = [c for e, c, o, g in results() if o == "probe" and not g]
    assert not missed, f"{len(missed)}/15 probes get through:\n" + "\n".join(missed)


def test_block_recall_at_least_98_percent():
    block = [(c, g) for e, c, o, g in results() if e == "BLOCK"]
    missed = [c for c, g in block if not g]
    recall = 1 - len(missed) / len(block)
    print(f"BLOCK recall {len(block) - len(missed)}/{len(block)} = {recall:.1%}")
    assert recall >= RECALL_TARGET, f"recall {recall:.1%}; not blocked:\n" + "\n".join(missed)


def test_no_allow_line_is_blocked():
    wrong = [c for e, c, o, g in results() if e == "ALLOW" and g]
    assert not wrong, "wrongly blocked:\n" + "\n".join(wrong)


def test_live_false_blocks_are_allowed():
    """Two real false blocks of the old guard (2026-10-22): a quoted '+ then' read as a +refspec, and a heredoc
    that only mentioned the ssh key folder read as access to it."""
    rows = []
    for line in (FIXTURES / "allow-live-false-blocks.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            expect, cmd, _ = line.split("\t")
            rows.append((expect, cmd.replace("\\n", "\n") if "<<" in cmd else cmd))
    assert len(rows) == 2 and all(e == "ALLOW" for e, _ in rows)
    wrong = [c for _, c in rows if blocked_by_any(c, PROTECTED)]
    assert not wrong, "wrongly blocked:\n" + "\n".join(wrong)
