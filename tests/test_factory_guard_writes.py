"""factory_guard.py R4 (T15): a factory session's Bash never writes into a protected folder, by any tool.

Before T15 only git was checked (factory_guard.py:111). Protected folder here: ~/private-repo.
Commands are only text handed to the hook.
"""
import os

from guardlib import KIT, WORK, factory_guard

HOME = os.path.expanduser("~")
PRIV = f"{HOME}/private-repo"


def bash(cmd, cwd=WORK, **extra):
    return factory_guard("Bash", {"command": cmd}, PRIV, cwd, **extra)[0]


WRITES = [
    "sed -i 's/a/b/' ~/private-repo/x.py",
    "sed -i '' -e 's/a/b/' ~/private-repo/CLAUDE.md",
    "sed --in-place 's/a/b/' $HOME/private-repo/x",
    "perl -pi -e 's/a/b/' ~/private-repo/x",
    "echo y > ~/private-repo/x",
    "echo y >> ~/private-repo/x",
    "echo y 2> ~/private-repo/err.log",
    "echo y &> ~/private-repo/x",
    "tee ~/private-repo/x < /dev/null",
    "echo y | tee -a ~/private-repo/x",
    "cp /tmp/x ~/private-repo/x",
    "cp -r /tmp/d ~/private-repo/",
    "cp -t ~/private-repo /tmp/x",
    "mv /tmp/x ~/private-repo/x",
    "mv ~/private-repo/x /tmp/x",
    "ln -s /tmp/x ~/private-repo/link",
    "install -m 644 /tmp/x ~/private-repo/x",
    "rsync -a /tmp/d/ ~/private-repo/d/",
    "touch ~/private-repo/x",
    "mkdir -p ~/private-repo/new",
    "rm ~/private-repo/x",
    "rmdir ~/private-repo/empty",
    "chmod +x ~/private-repo/x",
    "truncate -s 0 ~/private-repo/x",
    "dd if=/dev/zero of=~/private-repo/x count=1",
    "curl -o ~/private-repo/x https://example.com",
    "python3 -c \"open('" + PRIV + "/x','w').write('y')\"",
    "python3 -c \"import pathlib; pathlib.Path('" + PRIV + "/x').write_text('y')\"",
    "node -e 'require(\"fs\").writeFileSync(\"" + PRIV + "/x\", \"y\")'",
    "cd ~/private-repo && sed -i 's/a/b/' x",
    "cd ~/private-repo && echo y > x",
    "cd ~/private-repo && python3 -c \"open('x','w').write('y')\"",
    "cd ~/private-repo/scripts && touch new.py",
    "bash -c 'echo y > ~/private-repo/x'",
    "git -C ~/private-repo commit -m x",            # kept from before
    "cd ~/private-repo && git add -A",
]

ALLOWED = [
    "cat ~/private-repo/CLAUDE.md",
    "grep -rn foo ~/private-repo/src",
    "~/private-repo/scripts/safe-push.sh",
    "cp ~/private-repo/template.md /private/tmp/t1-wt/",
    "rsync -a ~/private-repo/docs/ /private/tmp/t1-wt/docs/",
    "sed -n 1,20p ~/private-repo/README.md",
    "sed -i 's/a/b/' src/app.py",
    "echo y > /private/tmp/t1-wt/x",
    "cd ~/private-repo && git status && cat x > /private/tmp/out.txt",
    "cd ~/private-repo && ls 2>/dev/null",
    "python3 -c \"print(open('" + PRIV + "/x').read())\"",
    "git -C ~/private-repo log --oneline -3",
    "cd ~/private-repo-notes && touch x",          # a sibling folder
    "echo 'never write to ~/private-repo/x' > notes.md",
]


def test_non_git_writes_into_a_protected_folder_are_denied():
    missed = [c for c in WRITES if not bash(c)]
    assert not missed, "not denied:\n" + "\n".join(missed)


def test_reads_and_writes_elsewhere_stay_allowed():
    wrong = [c for c in ALLOWED if bash(c)]
    assert not wrong, "wrongly denied:\n" + "\n".join(wrong)


def test_relative_paths_use_the_session_folder():
    assert bash("sed -i 's/a/b/' x.py", cwd=PRIV)
    assert bash("echo y > notes.md", cwd=f"{PRIV}/docs")
    assert not bash("echo y > notes.md", cwd=f"{WORK}/hello")


def test_the_kit_itself_is_protected_against_bash_writes():
    assert bash(f"echo x >> {KIT}/hooks/security-bash.sh")
    assert bash(f"sed -i 's/exit 2/exit 0/' {KIT}/hooks/guard_bash.py")


def test_deny_reason_names_the_protected_folder_rule():
    denied, reason = factory_guard("Bash", {"command": "echo y > ~/private-repo/x"}, PRIV)
    assert denied and reason.startswith("BLOCKED") and "protected" in reason


def test_owner_sessions_outside_the_factory_are_left_alone():
    assert not bash("echo y > x", cwd=PRIV, FACTORY_SESSION="")
