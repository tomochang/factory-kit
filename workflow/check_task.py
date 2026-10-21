#!/usr/bin/env python3
"""Check a factory task repo against the workflow (WORKFLOW.md, steps 3-10).

Usage: check_task.py <repo>   prints PASS/FAIL per check, exit 1 if any check fails.
"""
import os
import re
import subprocess
import sys
from collections import namedtuple

Result = namedtuple("Result", "name ok detail")

KGI_FIELDS = ("Metric", "Target", "Deadline", "How measured")
PLACEHOLDER = re.compile(r"T<n>|<[^<>\n]*[ |/][^<>\n]*>")
EVIDENCE = re.compile(r"\bev_[0-9a-f]{8,}")
NOT_APPLICABLE = re.compile(r"REA: not applicable\W+\w{3,}")
CODE_EXT = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".sh", ".swift", ".rb", ".java", ".kt")
LOCK_FILES = ("KGI.md", "SPEC.md", "ROADMAP.md")


def read(repo, name):
    try:
        with open(os.path.join(repo, name), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def check_doc(repo, name, extra=None):
    text = read(repo, name)
    if text is None:
        return Result(name, False, "missing")
    if PLACEHOLDER.search(text):
        return Result(name, False, "template placeholder left: " + PLACEHOLDER.search(text).group(0))
    if extra:
        problem = extra(text)
        if problem:
            return Result(name, False, problem)
    return Result(name, True, "")


def kgi_problem(text):
    for field in KGI_FIELDS:
        m = re.search(r"^- %s:[ \t]*(\S.*)?$" % re.escape(field), text, re.M)
        if not m or not m.group(1):
            return "KGI field empty: " + field
    return None


def reference_problem(text):
    if EVIDENCE.search(text) or NOT_APPLICABLE.search(text):
        return None
    return "no REA evidence ID (ev_...) and no 'REA: not applicable - <reason>'"


def is_test(path):
    base = os.path.basename(path)
    parts = path.split("/")
    return (base.startswith("test_") or re.search(r"(_test\.go|\.(test|spec)\.[jt]sx?|_test\.py)$", base)
            or "test" in parts[:-1] or "tests" in parts[:-1])


def history(repo):
    """Commits oldest first, each as the list of files it added."""
    out = subprocess.run(["git", "-C", repo, "log", "--reverse", "--diff-filter=A", "--name-only",
                          "--format=%x00%H"], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return [c.split("\n")[1:] for c in out.stdout.split("\0")[1:]]


def check_history(repo):
    commits = history(repo)
    if commits is None:
        return [Result("git history", False, "not a git repo")]
    added = [[f for f in c if f] for c in commits]
    results = []
    first = set(added[0]) if added else set()
    missing = [f for f in LOCK_FILES if f not in first]
    results.append(Result("spec locked first", not missing,
                          "first commit lacks " + ", ".join(missing) if missing else ""))

    def first_index(pred):
        return next((i for i, c in enumerate(added) if any(pred(f) for f in c)), None)

    code = first_index(lambda f: f.endswith(CODE_EXT) and not is_test(f))
    tests = first_index(is_test)
    if code is None:
        results.append(Result("tests first", True, "no code yet"))
    elif tests is None or tests > code:
        results.append(Result("tests first", False, "code committed before any test"))
    else:
        results.append(Result("tests first", True, ""))
    return results


def check(repo):
    return [
        check_doc(repo, "KGI.md", kgi_problem),
        check_doc(repo, "SPEC.md"),
        check_doc(repo, "ROADMAP.md"),
        check_doc(repo, "REFERENCE.md", reference_problem),
    ] + check_history(repo)


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    results = check(argv[1])
    for r in results:
        print(("PASS " if r.ok else "FAIL ") + r.name + (": " + r.detail if r.detail else ""))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
