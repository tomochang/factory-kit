"""start-watchdog.sh (shipped test) starts <dir>/watchdog/watchdog.py only when its sha256 matches the pin
(the kit folder is protected, so the factory cannot swap in unreviewed daemon code)."""
import hashlib
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "foreman" / "start-watchdog.sh"
CODE = "import pathlib, sys\npathlib.Path(sys.argv[0]).with_name('started').write_text('yes')\n"


def setup(tmp_path, code=CODE, pin=None):
    wd = tmp_path / "factory" / "watchdog"
    wd.mkdir(parents=True)
    (wd / "watchdog.py").write_text(code)
    pin_file = tmp_path / "watchdog.sha256"
    pin_file.write_text((pin or hashlib.sha256(code.encode()).hexdigest()) + "\n")
    return wd, pin_file


def start(tmp_path, pin_file):
    return subprocess.run(["bash", str(SCRIPT), str(tmp_path / "factory"), str(pin_file)],
                          capture_output=True, text=True, timeout=10)


def started(wd):
    for _ in range(50):
        if (wd / "started").exists():
            return True
        time.sleep(0.1)
    return False


def test_pinned_watchdog_is_started(tmp_path):
    wd, pin = setup(tmp_path)
    r = start(tmp_path, pin)
    assert r.returncode == 0, r.stderr
    assert started(wd)


def test_changed_watchdog_is_not_started_and_says_why(tmp_path):
    wd, pin = setup(tmp_path, pin="0" * 64)
    r = start(tmp_path, pin)
    assert r.returncode == 0
    assert "not started" in r.stderr and "sha256" in r.stderr
    time.sleep(1)
    assert not (wd / "started").exists()


def test_missing_pin_means_not_started(tmp_path):
    wd, pin = setup(tmp_path)
    pin.unlink()
    r = start(tmp_path, pin)
    assert "not started" in r.stderr
    time.sleep(1)
    assert not (wd / "started").exists()


SLEEPER = ("import pathlib, sys, time\npathlib.Path(sys.argv[0]).with_name('started').write_text('yes')\n"
           "time.sleep(20)\n")


def test_an_older_running_watchdog_is_named_with_the_restart_command(tmp_path):
    # T18: a new reviewed watchdog.py (the Codex watcher) only runs after the old process is restarted
    import hashlib
    import subprocess as sp
    wd, pin = setup(tmp_path, code=SLEEPER)
    try:
        assert start(tmp_path, pin).returncode == 0 and started(wd)
        r = start(tmp_path, pin)                                   # same version running: says nothing
        assert r.stderr == ""
        new = SLEEPER + "# v2\n"
        (wd / "watchdog.py").write_text(new)
        pin.write_text(hashlib.sha256(new.encode()).hexdigest() + "\n")
        r = start(tmp_path, pin)
        assert "older" in r.stderr and "pkill -f" in r.stderr and str(wd / "watchdog.py") in r.stderr
    finally:
        sp.run(["pkill", "-f", str(wd / "watchdog.py")])
