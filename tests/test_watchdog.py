"""watchdog.py (shipped test, generalised): alerts on foreman stop/start/restart and on a quiet channel.

Tests use a fake process list, a fake sender and a fake heartbeat only; they never call ps or ask-me.py.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

_saved = {k: os.environ.get(k) for k in ("TZ", "FACTORY_ENV", "ASK_ME_STATE")}
STATE = tempfile.mkdtemp()
os.environ.update(TZ="Asia/Tokyo", FACTORY_ENV="/nonexistent/factory.env", ASK_ME_STATE=STATE)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "watchdog"))
import datetime  # noqa: E402
import unittest  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import watchdog  # noqa: E402
from watchdog import Watchdog, foreman_pid, foreman_running  # noqa: E402

for _k, _v in _saved.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v

FOREMAN = "claude --channels plugin:whatsapp server:whatsapp"
OTHER = [(1, "/sbin/launchd"), (500, "/usr/bin/zsh"), (900, "ps -axo pid=,command=")]
UTC = datetime.timezone.utc


class FakeProcs:
    """Fake process list returning (pid, command) pairs."""

    def __init__(self, running=True, pid=4242):
        self.running = running
        self.pid = pid
        self.fail = False

    def __call__(self):
        if self.fail:
            raise RuntimeError("ps failed")
        return OTHER + ([(self.pid, FOREMAN)] if self.running else [])


class FakeSender:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def __call__(self, text):
        self.sent.append(text)
        if self.fail:
            raise RuntimeError("boom")


def fixed_clock():
    # 2026-10-21 07:05 UTC == 16:05 JST
    return datetime.datetime(2026, 10, 21, 7, 5, 30, tzinfo=UTC)


def make(running=True, fail=False):
    procs, sender, logs = FakeProcs(running), FakeSender(fail), []
    wd = Watchdog(list_processes=procs, send=sender, clock=fixed_clock,
                  sleep=lambda s: None, log=logs.append)
    return wd, procs, sender, logs


class WatchdogTest(unittest.TestCase):
    def setUp(self):
        # Guard: tests must never reach the real sender or ps.
        self._real_send = watchdog.send_message
        self._real_ps = watchdog.list_processes

        def forbidden(*a, **k):
            raise AssertionError("real sender/ps called in tests")
        watchdog.send_message = forbidden
        watchdog.list_processes = forbidden

    def tearDown(self):
        watchdog.send_message = self._real_send
        watchdog.list_processes = self._real_ps

    def test_no_message_at_startup_running(self):
        wd, _, sender, _ = make(running=True)
        wd.check_once()
        self.assertEqual(sender.sent, [])
        self.assertTrue(wd.running)

    def test_no_message_at_startup_stopped(self):
        wd, _, sender, _ = make(running=False)
        wd.check_once()
        self.assertEqual(sender.sent, [])
        self.assertFalse(wd.running)

    def test_running_to_stopped_sends_once_with_jst_time(self):
        wd, procs, sender, _ = make(running=True)
        wd.check_once()
        procs.running = False
        wd.check_once()
        self.assertEqual(sender.sent, ["Factory foreman stopped at 2026-10-21 16:05"])

    def test_no_repeat_while_stopped(self):
        wd, procs, sender, _ = make(running=True)
        wd.check_once()
        procs.running = False
        for _ in range(5):
            wd.check_once()
        self.assertEqual(len(sender.sent), 1)

    def test_recovery_sends_running_again_once(self):
        wd, procs, sender, _ = make(running=True)
        wd.check_once()
        procs.running = False
        wd.check_once()
        wd.check_once()
        procs.running = True
        wd.check_once()
        wd.check_once()
        self.assertEqual(sender.sent, [
            "Factory foreman stopped at 2026-10-21 16:05",
            "Factory foreman is running again",
        ])

    def test_no_message_while_running_steadily(self):
        wd, _, sender, _ = make(running=True)
        for _ in range(10):
            wd.check_once()
        self.assertEqual(sender.sent, [])

    def test_own_command_line_does_not_count(self):
        own = [
            (10, "/usr/bin/python3 /Users/x/factory/watchdog/watchdog.py --interval 15"),
            (11, "python3 watchdog.py server:whatsapp"),
        ]
        self.assertFalse(foreman_running(own))
        self.assertTrue(foreman_running(own + [(99, FOREMAN)]))
        self.assertEqual(foreman_pid(own + [(99, FOREMAN)]), 99)

        wd = Watchdog(list_processes=lambda: own, send=FakeSender(),
                      clock=fixed_clock, sleep=lambda s: None, log=lambda m: None)
        wd.check_once()
        self.assertFalse(wd.running)

    def test_send_failure_is_logged_and_watchdog_continues(self):
        wd, procs, sender, logs = make(running=True, fail=True)
        wd.check_once()
        procs.running = False
        wd.check_once()
        self.assertEqual(len(logs), 1)
        self.assertIn("sending failed", logs[0])
        procs.running = True
        wd.check_once()
        self.assertEqual(len(sender.sent), 2)

    def test_run_loop_uses_injected_sleep(self):
        sleeps = []
        procs, sender = FakeProcs(True), FakeSender()
        wd = Watchdog(list_processes=procs, send=sender, clock=fixed_clock,
                      sleep=sleeps.append, log=lambda m: None)
        wd.run(interval=7, iterations=3)
        self.assertEqual(sleeps, [7, 7])
        self.assertEqual(sender.sent, [])

    def test_restart_between_checks_sends_one_restarted_message(self):
        wd, procs, sender, _ = make(running=True)
        wd.check_once()
        procs.pid = 5555
        wd.check_once()
        wd.check_once()
        self.assertEqual(sender.sent, ["Factory foreman restarted at 2026-10-21 16:05"])
        self.assertEqual(wd.pid, 5555)

    def test_steady_pid_sends_nothing(self):
        wd, procs, sender, _ = make(running=True)
        for _ in range(5):
            wd.check_once()
        self.assertEqual(sender.sent, [])
        self.assertEqual(wd.pid, procs.pid)

    def test_no_restart_message_at_startup(self):
        wd, procs, sender, _ = make(running=True)
        procs.pid = 7777
        wd.check_once()
        self.assertEqual(sender.sent, [])
        self.assertEqual(wd.pid, 7777)

    def test_stopped_to_running_with_new_pid_is_running_again_not_restarted(self):
        wd, procs, sender, _ = make(running=True)
        wd.check_once()
        procs.running = False
        wd.check_once()
        procs.running, procs.pid = True, 8888
        wd.check_once()
        self.assertEqual(sender.sent, [
            "Factory foreman stopped at 2026-10-21 16:05",
            "Factory foreman is running again",
        ])
        self.assertFalse(any("restarted" in t for t in sender.sent))

    def test_failed_listing_keeps_old_state_and_pid(self):
        wd, procs, sender, logs = make(running=True)
        wd.check_once()
        procs.fail = True
        self.assertIsNone(wd.check_once())
        self.assertTrue(wd.running)
        self.assertEqual(wd.pid, 4242)
        self.assertIn("process list failed", logs[0])
        procs.fail = False
        wd.check_once()
        self.assertEqual(sender.sent, [])

    def test_restart_send_failure_is_logged_and_watchdog_continues(self):
        wd, procs, sender, logs = make(running=True, fail=True)
        wd.check_once()
        procs.pid = 5555
        wd.check_once()
        self.assertEqual(len(logs), 1)
        self.assertIn("sending failed", logs[0])
        procs.pid = 6666
        wd.check_once()
        self.assertEqual(len(sender.sent), 2)

    def test_lowest_pid_chosen_when_several_match(self):
        procs = [(300, FOREMAN), (120, FOREMAN + " --x"), (50, "zsh")]
        self.assertEqual(foreman_pid(procs), 120)
        self.assertIsNone(foreman_pid([(50, "zsh")]))

    def test_default_interval_is_15(self):
        sleeps = []
        wd = Watchdog(list_processes=FakeProcs(True), send=FakeSender(),
                      clock=fixed_clock, sleep=sleeps.append, log=lambda m: None)
        wd.run(iterations=2)
        self.assertEqual(sleeps, [15])
        captured = {}

        orig_init = watchdog.Watchdog.__init__
        orig_run = watchdog.Watchdog.run

        def fake_init(self_, *a, **k):
            orig_init(self_, list_processes=FakeProcs(True), send=FakeSender(),
                      clock=fixed_clock, sleep=lambda s: None, log=lambda m: None)

        def fake_run(self_, interval=None, iterations=None):
            captured["interval"] = interval

        watchdog.Watchdog.__init__ = fake_init
        watchdog.Watchdog.run = fake_run
        try:
            watchdog.main([])
        finally:
            watchdog.Watchdog.__init__ = orig_init
            watchdog.Watchdog.run = orig_run
        self.assertEqual(captured["interval"], 15)

    def test_clock_uses_the_configured_time_zone(self):
        self.assertEqual(watchdog.ZONE, ZoneInfo("Asia/Tokyo"))

    # --- kit additions: the channel going quiet (R6)

    def quiet_setup(self, ages, running=True):
        procs, sender, logs = FakeProcs(running), FakeSender(), []
        it = iter(ages)
        wd = Watchdog(list_processes=procs, send=sender, clock=fixed_clock, sleep=lambda s: None,
                      log=logs.append, heartbeat=lambda: next(it), quiet_after=600)
        return wd, procs, sender

    def test_quiet_channel_alerts_once_and_says_when_it_recovers(self):
        wd, _, sender = self.quiet_setup([5, 700, 760, 900, 3, 4])
        for _ in range(6):
            wd.check_once()
        self.assertEqual(len(sender.sent), 2, sender.sent)
        self.assertTrue(sender.sent[0].startswith("Factory channel quiet"), sender.sent[0])
        self.assertIn("2026-10-21 15:53", sender.sent[0])  # last read: 700 s before 16:05 JST
        self.assertEqual(sender.sent[1], "Factory channel is reading messages again")

    def test_unknown_heartbeat_sends_nothing(self):
        wd, _, sender = self.quiet_setup([None, None, None])
        for _ in range(3):
            wd.check_once()
        self.assertEqual(sender.sent, [])

    def test_no_quiet_alert_while_the_foreman_is_stopped(self):
        wd, _, sender = self.quiet_setup([5, 900, 900], running=False)
        for _ in range(3):
            wd.check_once()
        self.assertEqual(sender.sent, [])

    def test_default_heartbeat_is_the_channel_file_in_the_state_folder(self):
        p = Path(STATE) / "channel.heartbeat"
        p.write_text(str(time.time() - 1000))
        try:
            age = watchdog.heartbeat_age(STATE)
            self.assertTrue(990 < age < 1100, age)
        finally:
            p.unlink()
        self.assertIsNone(watchdog.heartbeat_age(STATE))

    def test_ask_me_is_the_kit_copy(self):
        self.assertEqual(Path(watchdog.ASK_ME), Path(__file__).resolve().parents[1] / "scripts" / "ask-me.py")


if __name__ == "__main__":
    unittest.main()
