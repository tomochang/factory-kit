import os
import subprocess
import tempfile
import unittest

import check_task

KGI = """# T9 KGI

## KGI

- Metric: words counted per second
- Target: 1,000,000
- Deadline: 2026-11-01
- How measured: bench.py on a 100 MB file
- Based on: assumption
"""
SPEC = "# T9 Spec\n\n## Request\n\ncount words\n"
ROADMAP = "# T9 Roadmap\n\n| M1 | cli | KGI | test_wc |\n"
REFERENCE = "# T9 Reference\n\n- `rea import-reference-source` -> evidence `ev_31e8f15333b7`\n"


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def write(repo, name, text):
    with open(os.path.join(repo, name), "w") as f:
        f.write(text)


def commit(repo, msg, files):
    for name, text in files.items():
        write(repo, name, text)
    git(repo, "add", *files)
    git(repo, "commit", "-q", "-m", msg)


class CheckTaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        git(self.repo, "init", "-q", "-b", "main")

    def tearDown(self):
        self.tmp.cleanup()

    def good_repo(self):
        commit(self.repo, "T9: lock spec", {"KGI.md": KGI, "SPEC.md": SPEC, "ROADMAP.md": ROADMAP})
        commit(self.repo, "T9: reference", {"REFERENCE.md": REFERENCE})
        commit(self.repo, "T9: tests", {"test_wc.py": "pass\n"})
        commit(self.repo, "T9: build", {"wc.py": "pass\n"})

    def failures(self):
        return [r for r in check_task.check(self.repo) if not r.ok]

    def test_complete_task_passes(self):
        self.good_repo()
        self.assertEqual(self.failures(), [])

    def test_missing_files_fail(self):
        commit(self.repo, "only code", {"wc.py": "pass\n"})
        names = {r.name for r in self.failures()}
        self.assertTrue({"KGI.md", "SPEC.md", "ROADMAP.md", "REFERENCE.md"} <= names)

    def test_kgi_with_empty_field_fails(self):
        self.good_repo()
        commit(self.repo, "edit", {"KGI.md": KGI.replace("Target: 1,000,000", "Target:")})
        self.assertIn("KGI.md", {r.name for r in self.failures()})

    def test_template_placeholder_fails(self):
        self.good_repo()
        commit(self.repo, "edit", {"SPEC.md": "# T<n> Spec\n"})
        self.assertIn("SPEC.md", {r.name for r in self.failures()})

    def test_reference_without_evidence_fails(self):
        self.good_repo()
        commit(self.repo, "edit", {"REFERENCE.md": "# T9 Reference\n\nread the code\n"})
        self.assertIn("REFERENCE.md", {r.name for r in self.failures()})

    def test_rea_not_applicable_with_reason_passes(self):
        self.good_repo()
        commit(self.repo, "edit", {"REFERENCE.md": "# T9\n\nREA: not applicable - closed web service\n"})
        self.assertEqual(self.failures(), [])

    def test_code_before_tests_fails(self):
        commit(self.repo, "T9: lock spec", {"KGI.md": KGI, "SPEC.md": SPEC, "ROADMAP.md": ROADMAP,
                                            "REFERENCE.md": REFERENCE})
        commit(self.repo, "T9: build", {"wc.py": "pass\n"})
        commit(self.repo, "T9: tests", {"test_wc.py": "pass\n"})
        self.assertIn("tests first", {r.name for r in self.failures()})

    def test_spec_not_in_first_commit_fails(self):
        commit(self.repo, "T9: tests", {"test_wc.py": "pass\n"})
        commit(self.repo, "T9: lock spec", {"KGI.md": KGI, "SPEC.md": SPEC, "ROADMAP.md": ROADMAP,
                                            "REFERENCE.md": REFERENCE})
        self.assertIn("spec locked first", {r.name for r in self.failures()})


if __name__ == "__main__":
    unittest.main()
