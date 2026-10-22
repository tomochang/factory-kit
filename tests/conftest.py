"""Shared helpers for the kit's pytest tests. Nothing here touches the real HOME, Beeper or agent configs."""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
TESTS = KIT / "tests"
sys.path.insert(0, str(TESTS))

import fake_beeper  # noqa: E402

CLAUDE_VERSION = "2.1.307 (Claude Code)"
CODEX_VERSION = "codex-cli 0.163.0"


def kit_files(src=KIT):
    r = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=src, capture_output=True,
                       text=True, check=True)
    return [f for f in r.stdout.split("\0") if f]


def make_kit(dest):
    """A fresh copy of the kit as a clone would have it (plus the channel's node_modules, to stay offline)."""
    dest = Path(dest)
    for f in kit_files():
        s, d = KIT / f, dest / f
        if s.is_symlink() or not s.exists():
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
    nm = KIT / "foreman" / "channel" / "node_modules"
    if nm.is_dir():
        shutil.copytree(nm, dest / "foreman" / "channel" / "node_modules", symlinks=True)
    return dest.resolve()


def fake_bins(d):
    """claude/codex stand-ins: print a version, and record any other call to <bin>/calls.log."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    for name, ver in (("claude", CLAUDE_VERSION), ("codex", CODEX_VERSION)):
        p = d / name
        p.write_text(
            "#!/bin/sh\n"
            f'if [ "$1" = "--version" ]; then echo "{ver}"; exit 0; fi\n'
            f'{{ echo "{name} cwd=$(pwd -P) session=${{FACTORY_SESSION:-}}"; for a in "$@"; do echo "arg=$a"; done; }}'
            f' >> "{d}/calls.log"\n'
            f'if [ ! -t 0 ]; then cat > "{d}/{name}.stdin"; fi\n')
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return d


def write_env(path, **values):
    lines = [f'{k}="{v}"' for k, v in values.items()]
    Path(path).write_text("\n".join(lines) + "\n")
    return Path(path)


def base_env(home, kit, port=None, **extra):
    """Environment for running kit scripts against a temporary HOME. Config values come from factory.env."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FACTORY_", "ASK_ME_", "BEEPER_"))}
    for k in ("OWNER_NAME", "CHAT_ID", "TOKEN_FILE", "BEEPER_BASE", "WORK_DIR", "PROTECTED_DIRS", "TZ",
              "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        env.pop(k, None)
    env["HOME"] = str(home)
    env["SHELL"] = "/bin/zsh"
    env.update(extra)
    return env


def setup_install(tmp_path, port=None, owner="Alex Example", protected=None):
    """Temp HOME + fresh kit copy + filled factory.env + token file. Returns (home, kit, env)."""
    home = tmp_path / "home"
    home.mkdir()
    kit = make_kit(tmp_path / "kit")
    token = home / ".config" / "factory-kit" / "beeper-token"
    token.parent.mkdir(parents=True)
    token.write_text("fake-token-for-tests\n")
    token.chmod(0o600)
    write_env(kit / "factory.env", OWNER_NAME=owner, CHAT_ID=fake_beeper.SELF, TOKEN_FILE="$HOME/.config/factory-kit/beeper-token",
              BEEPER_BASE=f"http://127.0.0.1:{port or 9}", WORK_DIR="$HOME/factory",
              PROTECTED_DIRS=protected or "$HOME/private-repo", TZ="UTC", ASK_ME_STATE="$HOME/.factory-kit/ask-me")
    bins = fake_bins(tmp_path / "bin")
    env = base_env(home, kit, PATH=f"{bins}:{os.environ['PATH']}")
    return home, kit, env


def run(cmd, env, cwd=None, input=None, timeout=60):
    return subprocess.run(cmd, env=env, cwd=cwd, input=input if input is not None else "", capture_output=True,
                          text=True, timeout=timeout)


@pytest.fixture
def beeper():
    fake = fake_beeper.Fake()
    srv = fake_beeper.serve(fake)
    fake.port = srv.server_port
    yield fake
    srv.shutdown()
