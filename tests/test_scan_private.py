"""scan_private.py: the repo holds 0 personal values (KPI), and real values are never printed."""
import subprocess
import sys

from conftest import KIT, base_env, run

SCAN = str(KIT / "scan_private.py")
# built at runtime so this file itself holds none of these shapes
SLACKISH = "xo" + "xb-1234567890-abcdefghij"
PRIV_ROOM = "!PrivRoom12345" + ":beeper.local"


def repo(tmp_path, files):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    for name, text in files.items():
        p = r / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return r


def scan(tmp_path, root, *args, home=None):
    home = home or (tmp_path / "emptyhome")
    home.mkdir(exist_ok=True)
    env = base_env(home, KIT)
    env["FACTORY_ENV"] = str(tmp_path / "no-such-factory.env")
    return run([sys.executable, SCAN, "--root", str(root), *args], env)


def test_clean_repo_passes(tmp_path):
    r = repo(tmp_path, {"README.md": "Fill in factory.env. Test chat !self:beeper.local, phone +15550000000.\n",
                        "a.py": "path = '/Users/x/factory/watchdog.py'\n"})
    out = scan(tmp_path, r, "--no-live")
    assert out.returncode == 0, out.stdout
    assert "0 findings" in out.stdout


def test_generic_patterns_are_found_without_printing_them(tmp_path):
    secret_chat = "!Qw3rTy9Zx81Lm" + ":beeper.local"
    phone = "+44" + "7700900123"
    r = repo(tmp_path, {"a.mjs": f"const CHAT = '{secret_chat}'\n", "b.md": f"call {phone}\n",
                        "c.json": '{"args": ["/Users' + '/somebody/repo/x.mjs"]}\n',
                        "d.sh": f"TOKEN={SLACKISH}\n"})
    out = scan(tmp_path, r, "--no-live")
    assert out.returncode == 1
    for kind in ("chat ID at a.mjs:1", "phone number at b.md:1", "absolute home path at c.json:1",
                 "token-shaped string at d.sh:1"):
        assert kind in out.stdout, out.stdout
    for value in (secret_chat, phone, "somebody", SLACKISH):
        assert value not in out.stdout


def test_listed_values_are_found_and_not_printed(tmp_path):
    r = repo(tmp_path, {"notes.md": "deploy key is hunter2-very-secret\n"})
    vals = tmp_path / "values.txt"
    vals.write_text("hunter2-very-secret\n")
    out = scan(tmp_path, r, "--no-live", "--values", str(vals))
    assert out.returncode == 1 and "listed value at notes.md:1" in out.stdout
    assert "hunter2" not in out.stdout


def live_home(tmp_path):
    """A HOME whose agent configs point at a private repo with an ask-me script and a token, like a real Mac."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir()
    (home / "vault" / "scripts").mkdir(parents=True)
    (home / "vault" / "credentials").mkdir()
    (home / ".claude" / "CLAUDE.md").write_text(
        "- When a decision needs Zorrino-san, ask: `~/vault/scripts/ask-me.py --from x \"q\"`\n")
    (home / ".codex" / "hooks.json").write_text(
        '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 %s/vault/hooks/stop.py"}]}]}}'
        % home)
    (home / "vault" / "scripts" / "ask-me.py").write_text(
        'CHAT = os.environ.get("ASK_ME_CHAT", "%s")  # WhatsApp: Zorrino Quill (self)\n' % PRIV_ROOM +
        'OWNER_WORDS = {"zorrino", "quill"}\n')
    (home / "vault" / "credentials" / "beeper-token").write_text("tok-SUPER-private-998877\n")
    return home


def test_real_values_from_the_live_config_are_found(tmp_path):
    home = live_home(tmp_path)
    r = repo(tmp_path, {
        "a.py": 'CHAT = "%s"\n' % PRIV_ROOM,
        "b.md": "Ask Zorrino before pushing.\n",
        "c.sh": 'TOKEN_FILE="$HOME/vault/credentials/beeper-token"\n',
        "d.txt": "tok-SUPER-private-998877\n",
        "e.md": "Messages from quill's chat\n",
        "T13-SPEC.md": "Out of scope: changing ~/vault.\n",  # locked task docs may name the folder
    })
    out = scan(tmp_path, r, home=home)
    assert out.returncode == 1, out.stdout
    for where in ("chat ID at a.py:1", "owner name at b.md:1", "private folder path at c.sh:1", "token at d.txt:1",
                  "owner name at e.md:1"):
        assert where in out.stdout, out.stdout
    assert "T13-SPEC.md" not in out.stdout
    for value in ("PrivRoom", "Zorrino", "zorrino", "SUPER", "quill", "vault"):
        assert value not in out.stdout


def test_the_kit_repo_itself_is_clean():
    out = run([sys.executable, SCAN], env=None, cwd=KIT)
    assert out.returncode == 0, out.stdout
    assert "0 findings" in out.stdout


def test_staged_content_is_scanned_even_when_the_working_tree_is_clean(tmp_path):
    """What a commit holds is the index; a value staged and then removed only from the file still counts."""
    r = repo(tmp_path, {"a.md": "call " + "+44" + "7700900111\n"})
    subprocess.run(["git", "add", "a.md"], cwd=r, check=True)
    (r / "a.md").write_text("clean now\n")
    out = scan(tmp_path, r, "--no-live")
    assert out.returncode == 1 and "phone number at a.md:1 (staged)" in out.stdout
