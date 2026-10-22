import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "safe-push.sh"


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def setup_repos(tmp_path):
    """bare remote; `main` = the shared checkout; `wt` = an agent worktree; `other` = another writer."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    for name in ("main", "other"):
        git(tmp_path, "clone", "-q", str(remote), name)
        git(tmp_path / name, "config", "user.email", "t@example.com")
        git(tmp_path / name, "config", "user.name", "t")
    main, other = tmp_path / "main", tmp_path / "other"
    (main / "a.txt").write_text("".join(f"{i}\n" for i in range(1, 11)))
    (main / "b.txt").write_text("b\n")
    git(main, "add", ".")
    git(main, "commit", "-qm", "init")
    git(main, "push", "-q", "origin", "main")
    git(other, "pull", "-q")
    wt = tmp_path / "wt"
    git(main, "worktree", "add", "-q", "-b", "agent/a", str(wt), "origin/main")
    return remote, main, wt, other


def commit(repo, path, text, msg, allow_empty=False):
    if allow_empty:
        git(repo, "commit", "-q", "--allow-empty", "-m", msg)
        return
    (repo / path).write_text(text)
    git(repo, "add", path)
    git(repo, "commit", "-qm", msg)


def run(repo, *args, env_extra=None):
    env = os.environ.copy()
    env["SAFE_PUSH_BACKOFF"] = "0"
    env.update(env_extra or {})
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=repo, capture_output=True, text=True, env=env)


def remote_subjects(remote):
    return git(remote, "log", "--format=%s", "main").stdout.split("\n")


def remote_head(remote):
    return git(remote, "rev-parse", "main").stdout


def remote_file(remote, path):
    return git(remote, "show", f"main:{path}").stdout


def test_pushes_onto_advanced_remote_and_worktree_ends_on_origin(tmp_path):
    remote, _, wt, other = setup_repos(tmp_path)
    commit(other, "b.txt", "b2\n", "other change")
    git(other, "push", "-q")
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "agent change" in remote_subjects(remote) and "other change" in remote_subjects(remote)
    assert git(wt, "rev-parse", "HEAD").stdout == remote_head(remote)
    assert "pushed 1 commit(s)" in r.stdout


def test_refuses_in_shared_main_checkout(tmp_path):
    """SP-2/3/7/8/10: never push from the checkout several sessions share."""
    remote, main, _, _ = setup_repos(tmp_path)
    before = remote_head(remote)
    commit(main, "c.txt", "c\n", "shared change")
    r = run(main)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "worktree" in r.stderr
    assert remote_head(remote) == before


def test_refuses_with_uncommitted_tracked_changes(tmp_path):
    remote, _, wt, _ = setup_repos(tmp_path)
    before = remote_head(remote)
    commit(wt, "c.txt", "c\n", "agent change")
    (wt / "b.txt").write_text("wip\n")
    r = run(wt)
    assert r.returncode == 2
    assert remote_head(remote) == before
    (wt / "b.txt").write_text("b\n")
    (wt / "a.txt").write_text("staged\n")
    git(wt, "add", "a.txt")
    assert run(wt).returncode == 2


def test_refuses_merge_commits_in_range(tmp_path):
    """SP-5: changes inside a merge commit must not be dropped silently."""
    remote, _, wt, _ = setup_repos(tmp_path)
    before = remote_head(remote)
    git(wt, "switch", "-q", "-c", "side")
    commit(wt, "s.txt", "s\n", "side")
    git(wt, "switch", "-q", "agent/a")
    git(wt, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    r = run(wt)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "merge" in r.stderr
    assert remote_head(remote) == before


def test_commit_matching_a_reverted_upstream_change_is_refused(tmp_path):
    """SP-1: an identical patch that upstream added and reverted is neither dropped nor pushed silently."""
    remote, _, wt, other = setup_repos(tmp_path)
    commit(other, "x.txt", "X\n", "upstream adds X")
    git(other, "revert", "--no-edit", "HEAD")
    git(other, "push", "-q")
    before = remote_head(remote)
    commit(wt, "x.txt", "X\n", "agent adds X")
    r = run(wt)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "already upstream" in r.stderr
    assert remote_head(remote) == before


def test_consecutive_pushes_never_duplicate(tmp_path):
    """SP-2/3"""
    remote, _, wt, other = setup_repos(tmp_path)
    commit(other, "a.txt", "1\n2\nO\n4\n5\n6\n7\n8\n9\n10\n", "other line 3")
    git(other, "push", "-q")
    commit(wt, "a.txt", "1\n2\n3\n4\nL\n6\n7\n8\n9\n10\n", "agent line 5")
    assert run(wt).returncode == 0
    git(other, "pull", "-q", "--rebase")
    commit(other, "b.txt", "b2\n", "other b")
    git(other, "push", "-q")
    commit(wt, "c.txt", "c\n", "agent c")
    r = run(wt)
    assert r.returncode == 0, r.stdout + r.stderr
    subjects = remote_subjects(remote)
    assert subjects.count("agent line 5") == 1 and subjects.count("agent c") == 1


def test_conflict_aborts_and_restores_worktree(tmp_path):
    remote, _, wt, other = setup_repos(tmp_path)
    commit(other, "b.txt", "other\n", "other edit")
    git(other, "push", "-q")
    before = remote_head(remote)
    commit(wt, "b.txt", "agent\n", "agent edit")
    head = git(wt, "rev-parse", "HEAD").stdout
    r = run(wt)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "b.txt" in r.stderr
    assert remote_head(remote) == before
    assert git(wt, "rev-parse", "HEAD").stdout == head
    assert not (Path(git(wt, "rev-parse", "--git-dir").stdout.strip()) / "rebase-merge").exists()


def test_failing_test_blocks_push(tmp_path):
    remote, _, wt, _ = setup_repos(tmp_path)
    before = remote_head(remote)
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt, "--test", "false")
    assert r.returncode == 4
    assert remote_head(remote) == before


def test_retries_after_losing_a_race(tmp_path):
    """The test command lets `other` win the race, so the first push is non-fast-forward."""
    remote, _, wt, other = setup_repos(tmp_path)
    marker = tmp_path / "raced"
    race = (f'[ -e "{marker}" ] || {{ touch "{marker}"; cd "{other}" && echo r > r.txt && '
            f'git add r.txt && git commit -qm "other wins race" && git push -q; }}')
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt, "--test", race)
    assert r.returncode == 0, r.stdout + r.stderr
    subjects = remote_subjects(remote)
    assert "other wins race" in subjects and "agent change" in subjects


def test_non_race_rejection_is_not_retried(tmp_path):
    """SP-20"""
    remote, _, wt, _ = setup_repos(tmp_path)
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho policy says no >&2\nexit 1\n")
    hook.chmod(0o755)
    count = tmp_path / "count"
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt, "--test", f'echo x >> "{count}"')
    assert r.returncode == 6, r.stdout + r.stderr
    assert count.read_text().count("x") == 1


def test_initially_empty_commit_does_not_block(tmp_path):
    """SP-9"""
    remote, _, wt, _ = setup_repos(tmp_path)
    commit(wt, None, None, "marker", allow_empty=True)
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "agent change" in remote_subjects(remote)


def test_signal_during_test_prevents_push(tmp_path):
    """SP-14: INT reaching only the script (its pid is $PPID inside the test command)."""
    remote, _, wt, _ = setup_repos(tmp_path)
    before = remote_head(remote)
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt, "--test", "kill -INT $PPID; sleep 1")
    assert r.returncode != 0
    assert remote_head(remote) == before


def test_bad_arguments_exit_2(tmp_path):
    """SP-19/21"""
    _, _, wt, _ = setup_repos(tmp_path)
    commit(wt, "c.txt", "c\n", "agent change")
    assert run(wt, "--test").returncode == 2
    assert run(wt, "--bogus").returncode == 2
    assert run(wt, env_extra={"SAFE_PUSH_TRIES": "five"}).returncode == 2


def test_nothing_to_push(tmp_path):
    _, _, wt, _ = setup_repos(tmp_path)
    r = run(wt)
    assert r.returncode == 0
    assert "nothing to push" in r.stdout


def test_leaves_no_files_outside_the_worktree(tmp_path):
    """SP-11/15: no temp dirs, no prune of other worktrees."""
    _, main, wt, _ = setup_repos(tmp_path)
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    commit(wt, "c.txt", "c\n", "agent change")
    assert run(wt, env_extra={"TMPDIR": str(tmpdir)}).returncode == 0
    assert list(tmpdir.iterdir()) == []
    assert len(git(main, "worktree", "list").stdout.strip().splitlines()) == 2


def test_refuses_while_a_rebase_is_in_progress_and_keeps_it(tmp_path):
    """R2-1/R2-2: never treat a half-done rebase as 'nothing to push', never abort the agent's own rebase."""
    remote, _, wt, other = setup_repos(tmp_path)
    commit(other, "b.txt", "b2\n", "other")
    git(other, "push", "-q")
    commit(wt, "c.txt", "c\n", "w1")
    commit(wt, "d.txt", "d\n", "w2")
    git(wt, "fetch", "-q")
    env = os.environ.copy()
    env["GIT_SEQUENCE_EDITOR"] = "sed -i.bak s/^pick/edit/"
    subprocess.run(["git", "rebase", "-i", "origin/main"], cwd=wt, env=env, capture_output=True)
    before = remote_head(remote)
    r = run(wt)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "in progress" in r.stderr
    gitdir = Path(git(wt, "rev-parse", "--absolute-git-dir").stdout.strip())
    assert (gitdir / "rebase-merge").exists()
    assert remote_head(remote) == before


def test_refuses_untracked_files_so_test_sees_the_pushed_tree(tmp_path):
    """R2-3"""
    remote, _, wt, _ = setup_repos(tmp_path)
    before = remote_head(remote)
    commit(wt, "app.txt", "app\n", "app only")
    (wt / "helper.txt").write_text("not committed\n")
    r = run(wt, "--test", "test -f helper.txt")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "untracked" in r.stderr
    assert remote_head(remote) == before


def test_server_side_ref_race_is_retried(tmp_path):
    """R2-4: the race is only detected by the server ('cannot lock ref')."""
    remote, _, wt, other = setup_repos(tmp_path)
    marker = tmp_path / "raced"
    hook = Path(git(wt, "rev-parse", "--git-common-dir").stdout.strip())
    hook = (wt / hook if not hook.is_absolute() else hook) / "hooks" / "pre-push"
    hook.write_text(f'#!/bin/sh\n[ -e "{marker}" ] && exit 0\ntouch "{marker}"\n'
                    f'unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_QUARANTINE_PATH; cd "{other}" && echo r > r.txt && git add r.txt && git commit -qm "other wins race" && git push -q\n')
    hook.chmod(0o755)
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt)
    assert r.returncode == 0, r.stdout + r.stderr
    assert {"other wins race", "agent change"} <= set(remote_subjects(remote))


def test_test_that_dirties_the_tree_fails_instead_of_fake_conflict(tmp_path):
    """R2-5"""
    remote, _, wt, _ = setup_repos(tmp_path)
    before = remote_head(remote)
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt, "--test", "echo changed > b.txt")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "changed tracked files" in r.stderr
    assert remote_head(remote) == before


def test_bad_branch_is_refused_not_nothing_to_push(tmp_path):
    """R2-6/R2-7"""
    _, _, wt, other = setup_repos(tmp_path)
    git(other, "tag", "v1")
    git(other, "push", "-q", "origin", "v1")
    commit(wt, "c.txt", "c\n", "agent change")
    assert run(wt, "--branch", "v1").returncode in (2, 6)
    pwn = tmp_path / "PWNED"
    r = run(wt, "--branch", f"--upload-pack=touch {pwn}; git-upload-pack")
    assert r.returncode == 2
    assert not pwn.exists()


def test_interrupt_during_push_reports_truthfully(tmp_path):
    """R2-8: if the push landed despite the interrupt, say so."""
    remote, _, wt, _ = setup_repos(tmp_path)
    hookdir = Path(git(wt, "rev-parse", "--git-common-dir").stdout.strip())
    hookdir = (wt / hookdir if not hookdir.is_absolute() else hookdir) / "hooks"
    (hookdir / "pre-push").write_text("#!/bin/sh\nkill -TERM $SAFE_PUSH_PID\nsleep 1\n")
    (hookdir / "pre-push").chmod(0o755)
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt)
    if "agent change" in remote_subjects(remote):
        assert r.returncode == 0 and "pushed" in r.stdout, r.stdout + r.stderr
    else:
        assert r.returncode == 130 and "nothing pushed" in r.stderr


def test_commit_already_upstream_by_patch_is_refused_not_guessed(tmp_path):
    """SP-1 vs R2-9: a local commit whose patch is already upstream (maybe reverted on purpose) needs a human."""
    remote, _, wt, other = setup_repos(tmp_path)
    commit(wt, "x.txt", "X\n", "agent adds X")
    sha = git(wt, "rev-parse", "HEAD").stdout.strip()
    git(other, "fetch", "-q", str(wt), "agent/a")
    git(other, "cherry-pick", "-x", sha)
    git(other, "revert", "--no-edit", "HEAD")
    git(other, "push", "-q")
    before = remote_head(remote)
    r = run(wt)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "already upstream" in r.stderr
    assert remote_head(remote) == before


def test_env_git_dir_override_is_refused(tmp_path):
    """R2-11"""
    remote, main, wt, _ = setup_repos(tmp_path)
    before = remote_head(remote)
    commit(wt, "c.txt", "c\n", "agent change")
    gitdir = git(wt, "rev-parse", "--absolute-git-dir").stdout.strip()
    r = run(main, env_extra={"GIT_DIR": gitdir, "GIT_WORK_TREE": str(wt)})
    assert r.returncode == 2
    assert remote_head(remote) == before


def test_shared_checkout_fast_forwards_after_push(tmp_path):
    remote, main, wt, _ = setup_repos(tmp_path)
    (main / "notes.txt").write_text("another session's wip\n")  # untracked, unrelated
    (main / "b.txt").write_text("unrelated edit\n")             # tracked, untouched by the push
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt)
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(main, "rev-parse", "HEAD").stdout == remote_head(remote)
    assert (main / "c.txt").read_text() == "c\n"
    assert (main / "b.txt").read_text() == "unrelated edit\n"
    assert (main / "notes.txt").exists()
    assert "updated shared checkout" in r.stdout


def test_shared_checkout_with_local_commits_is_left_alone(tmp_path):
    remote, main, wt, _ = setup_repos(tmp_path)
    commit(main, "m.txt", "m\n", "local record commit")
    before = git(main, "rev-parse", "HEAD").stdout
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "agent change" in remote_subjects(remote)
    assert git(main, "rev-parse", "HEAD").stdout == before
    assert "shared checkout not updated" in r.stdout


def test_shared_checkout_change_in_the_way_is_preserved(tmp_path):
    remote, main, wt, _ = setup_repos(tmp_path)
    (main / "b.txt").write_text("another session's edit\n")
    before = git(main, "rev-parse", "HEAD").stdout
    commit(wt, "b.txt", "agent\n", "agent edits b")
    r = run(wt)
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(main, "rev-parse", "HEAD").stdout == before
    assert (main / "b.txt").read_text() == "another session's edit\n"
    assert "shared checkout not updated" in r.stdout


def test_shared_checkout_on_another_branch_is_left_alone(tmp_path):
    _, main, wt, _ = setup_repos(tmp_path)
    git(main, "switch", "-q", "-c", "experiment")
    before = git(main, "rev-parse", "HEAD").stdout
    commit(wt, "c.txt", "c\n", "agent change")
    r = run(wt)
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(main, "rev-parse", "HEAD").stdout == before
    assert git(main, "symbolic-ref", "--short", "HEAD").stdout.strip() == "experiment"


def test_repo_ignores_claude_worktrees_and_unions_interactions():
    assert git(ROOT, "check-ignore", "-q", ".claude/worktrees/x/a.txt", check=False).returncode == 0
    out = git(ROOT, "check-attr", "merge", "private/interactions/2026-10.jsonl").stdout
    assert out.strip().endswith("union")
