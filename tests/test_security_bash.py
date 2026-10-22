"""security-bash.sh: destructive git ref changes (force push, remote/branch delete, ref deletion).

Raw-text regex accident guard (fails closed). False positives on commands that merely MENTION these
strings (commit messages, echo, grep) are accepted, as with the previous --force rule.
Shipped with the kit (sections 1-6 of the guard only).
"""
import json
import pathlib
import subprocess

HOOK = str(pathlib.Path(__file__).resolve().parent.parent / "hooks" / "security-bash.sh")


def run(cmd):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    return subprocess.run(["bash", HOOK], input=payload, capture_output=True, text=True)


BLOCKED = [
    # force push: every spelling
    "git push -f origin main",
    "git push origin main -f",
    "git push -uf origin main",
    "git push -fq origin main",
    "git push --force origin main",
    "git push --force-with-lease origin main",
    "git push origin +main",
    "git push origin +HEAD:main",
    "git push origin '+refs/heads/main:refs/heads/main'",
    "git push origin \"+main\"",
    "git push origin main:main +other:other",
    "git push origin {+main,}",
    "git push 2>&1 --force origin main",
    "git push origin main >&2 -f",
    "git push origin main -f>/dev/null",
    "git push origin HEAD@{0}:main -f",
    "git push -o ci#1 --force origin main",
    "git push -$'f' origin main",
    "git push origin $'+main'",
    "git -C /tmp/wt push -f origin HEAD:main",
    "git --git-dir .git push -f origin main",
    "git --no-pager push -f",
    "cd /tmp/wt && git push -f origin main",
    "GIT_TRACE=1 git push -f origin main",
    "bash -c 'git push -f origin main'",
    "ssh host 'git push --force origin main'",
    "bash <<< 'git push -f origin main'",
    "python3 -c \"import os; os.system('git push -f origin main')\"",
    "echo `git push -f origin main`",
    "/usr/bin/git push -f origin main",
    # mirror / prune, including unique-prefix abbreviations git accepts
    "git push --mirror origin",
    "git push --mir origin",
    "git push --prune origin 'refs/heads/*:refs/heads/*'",
    "git push --pru origin 'refs/heads/*:refs/heads/*'",
    # remote delete
    "git push origin --delete old",
    "git push origin --del old",
    "git push origin --dele old",
    "git push origin -d old",
    "git push origin :old",
    "git push origin :refs/heads/old",
    # force via config, aliases, plumbing
    "git -c 'remote.origin.push=+refs/heads/*:refs/heads/*' push origin",
    "git -c remote.origin.push=+main:main push",
    "git -c remote.origin.mirror=true push origin",
    "git config remote.origin.mirror true",
    "git config --add remote.origin.push '+refs/heads/*:refs/heads/*'",
    "git -c 'alias.p=push -f' p origin main",
    "git config alias.pf 'push -f'",
    "git send-pack --force ../remote.git main",
    "git send-pack ../remote.git +main",
    # GitHub API ref deletion / forced update
    "gh api -X DELETE repos/o/r/git/refs/heads/main",
    "gh api --method DELETE repos/o/r/git/refs/heads/x",
    "gh api --method PATCH repos/o/r/git/refs/heads/main -F force=true -f sha=x",
    # review round 2: multi-line (backslash-newline continuation)
    "git push origin main \\\n  -f",
    "git push origin main \\\n  --force",
    "git \\\n push -f origin main",
    "git push \\\n  origin \\\n  --delete old",
    "git push \\\n origin +main",
    "git push origin \\\n :old",
    "git push \\\n --mirror origin",
    "gh api -X DELETE \\\n  repos/o/r/git/refs/heads/main",
    # review round 2: every abbreviation git accepts
    "git push origin --delet old",
    "git push --mi origin",
    "git push --mirr origin",
    "git push --mirro origin",
    "git push --prun origin 'refs/heads/*:refs/heads/*'",
    # review round 2: bundled -d
    "git push -ud origin old",
    "git push -dq origin old",
    # review round 2: mirror via any value / remote add
    "git -c remote.origin.mirror=yes push origin",
    "git -c remote.origin.mirror push origin",
    "git config remote.origin.mirror 1",
    "git remote add --mirror=push backup https://example.com/r.git",
    # review round 2: forced branch delete / move, other spellings
    "git branch -d -f old",
    "git branch -fd old",
    "git branch -qD old",
    "git branch -d --force old",
    "git -C . branch -Dq old",
    "git branch -f main HEAD~3",
    # review round 2: gh api method spellings
    "gh api --method=DELETE repos/o/r/git/refs/heads/main",
    "gh api -X delete repos/o/r/git/refs/heads/main",
    "gh api -X 'DELETE' repos/o/r/git/refs/heads/main",
    "gh api --method \"PATCH\" repos/o/r/git/refs/heads/main -F force=true",
    # local ref deletion
    "git update-ref -d refs/heads/feature",
    "git update-ref \"-d\" refs/heads/feature",
    "git update-ref --stdin < /tmp/cmds",
    "git branch -D feature",
    "git branch -df feature",
    "git branch --delete --force feature",
]

ALLOWED = [
    "git push origin main",
    "git push -q origin HEAD:main",
    "git push -u origin feature",
    "git push origin HEAD:refs/heads/main",
    "git push -q origin -- HEAD:refs/heads/main",
    "git push origin feature-f",
    "git push origin fix-delete-button",
    "git push --follow-tags origin main",
    "git push --tags origin",
    "git push --dry-run origin main",
    "git pull --rebase origin main",
    "git fetch --prune origin",
    "git log --format='%h %s'",
    "git update-ref refs/heads/x abc123",
    "git branch -d merged-branch",
    "git config user.name 'T'",
    "gh api repos/o/r/git/refs/heads/main",
    "gh pr list",
    "gh api -X PATCH repos/o/r/issues/1 -f state=closed",
    "gh api --method DELETE repos/o/r/labels/old",
    "scripts/safe-push.sh --test 'python3 -m pytest -q'",
    "ls -la && date",
    # review round 2: false positives seen in real history - an unrelated later command on the line
    "git push origin main && rm -f /tmp/x",
    "git push origin main && cp -f a b",
    "git push origin main | tail -f log",
    "git push -q origin main && git log | cut -d: -f1",
    "git push origin main && date -d tomorrow",
    "git push origin main && [ -d build ]",
    "git push origin main && docker run -d img",
    "git push origin main && git branch -d feature",
    "git push origin main && git fetch --prune",
    "git -C /tmp/wt push -q origin HEAD:main; git worktree remove --force /tmp/wt",
    "git push -q origin main && python3 -c \"print({'text': 1})\"",
    "git push origin main; tool --tool-input-json '{\"eventId\":\"x\"}'",
    "git push origin HEAD:feature-d",
    "git push origin feature:feature",
    # review round 2: other subcommands / words containing push
    "git stash push -m ':bug: fix'",
    "git commit -m 'safe-push + union merge'",
    "git commit -m 'feat: safe-push (worktree only) + tests'",
    "git branch --delete merged-branch",
    "git config --get alias.x",
    "gh pr merge 12 --squash --delete-branch",
    "gh api -X PATCH repos/o/r/pulls/3 -f state=closed",
]

# Deliberate obfuscation is OUT OF SCOPE for this accident guard (documented, not tested):
# quoting/escaping inside words (-\"f\", g\\it, p'u'sh, $'\\x66'), variables and command
# substitution ($F, \"$@\", $nothing:old), xargs/printf feeding flags, GIT_CONFIG_* env vars,
# shell functions/aliases defined on an earlier line, curl to the GitHub API, gh graphql.


def test_blocks():
    missed = [c for c in BLOCKED if run(c).returncode != 2]
    assert not missed, "not blocked:\n" + "\n".join(missed)


def test_allows():
    wrong = [c for c in ALLOWED if run(c).returncode != 0]
    assert not wrong, "wrongly blocked:\n" + "\n".join(wrong)


def test_block_message():
    r = run("git push -f origin main")
    assert "BLOCKED" in r.stderr


# --- sections 1 and 3-6 (generic), and sections 7-8 of the owner's own policy are not shipped (R8)
OTHER_BLOCKED = [
    "rm -rf build/",
    "git reset --hard HEAD~1",
    "git clean -fd",
    "git commit --no-verify -m x",
    "psql -c 'DROP TABLE users'",
    "cat ~/.ssh/id_rsa",
    "cat app/.env",
    "printenv",
    "echo $API_TOKEN",
    "terraform destroy",
]
OTHER_ALLOWED = [
    "rm build/a.o",
    "cat README.md",
    "python3 -m pytest -q",
    "claude --version",
    "codex --version",
    "curl -s http://127.0.0.1:23373/v1/accounts",
]


def test_generic_sections_block():
    missed = [c for c in OTHER_BLOCKED if run(c).returncode != 2]
    assert not missed, "not blocked:\n" + "\n".join(missed)


def test_generic_sections_allow():
    wrong = [c for c in OTHER_ALLOWED if run(c).returncode != 0]
    assert not wrong, "wrongly blocked:\n" + "\n".join(wrong)


def test_owner_only_sections_are_not_shipped():
    text = pathlib.Path(HOOK).read_text()
    for word in ("gog", "MAIL_APPROVED", "claude-vps", "claude-delegate", "ALLOW_CLAUDE_P", "line-send", "=== 7.",
                 "=== 8."):
        assert word not in text, word


def test_empty_or_non_bash_input_is_allowed():
    r = subprocess.run(["bash", HOOK], input=json.dumps({"tool_name": "Read", "tool_input": {}}),
                       capture_output=True, text=True)
    assert r.returncode == 0
