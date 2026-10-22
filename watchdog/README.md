# Factory foreman watchdog

`watchdog.py` checks every 15 seconds whether the factory foreman is running (any process whose command line
contains `server:whatsapp`, from `ps -axo pid=,command=`; the watchdog's own process never counts) and remembers
its PID. Messages go to the owner's own chat through `../scripts/ask-me.py --from watchdog --tell`:

- running -> stopped: `Factory foreman stopped at YYYY-MM-DD HH:MM` (time zone `TZ` from factory.env)
- stopped -> running: `Factory foreman is running again`
- a restart between two checks (new PID): `Factory foreman restarted at ...`
- the foreman runs but the channel has not read the chat for 10 minutes (`--quiet-after`): `Factory channel
  quiet: ...`, and `Factory channel is reading messages again` once it reads again. The channel writes
  `<ASK_ME_STATE>/channel.heartbeat` after every successful read.
- nothing at startup or while nothing changes; a failed send is logged and the watchdog keeps running.

`foreman/foreman.sh` starts it once through `foreman/start-watchdog.sh`, and only when `watchdog.py` matches the
pin in `watchdog.sha256` (the kit folder is protected from factory edits). After reviewing a change:

    shasum -a 256 watchdog/watchdog.py | cut -d' ' -f1 > watchdog/watchdog.sha256

Usage: `python3 watchdog.py [--interval 15] [--quiet-after 600] [--once]`. Stop it: `pkill -f factory-kit/watchdog/watchdog.py`.
Tests: `tests/test_watchdog.py` (fake process list, sender and heartbeat only).
