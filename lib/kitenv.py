#!/usr/bin/env python3
"""Kit settings: <kit>/factory.env (or the file named by FACTORY_ENV), with the real environment winning.

Like the official Telegram channel plugin (docs/t13/REFERENCE.md 1-3): one per-user file outside git, real env wins,
and a missing value is reported with the file's name. `$HOME` and `~` in values are expanded.

Usage: kitenv.py json | get KEY | check
"""
import json
import os
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
REQUIRED = ("OWNER_NAME", "CHAT_ID", "TOKEN_FILE")
DEFAULTS = {
    "BEEPER_BASE": "http://127.0.0.1:23373",
    "WORK_DIR": "~/factory",
    "PROTECTED_DIRS": "",
    "TZ": "UTC",
    "ASK_ME_STATE": "~/.factory-kit/ask-me",
    "OWNER_NAME": "",
    "CHAT_ID": "",
    "TOKEN_FILE": "",
    "CODEX_SANDBOX": "0",
    # WhatsApp reply guard (hooks/reply_guard.py, T22)
    "REPLY_MAX_CHARS": "700",
    "REPLY_MAX_LINES": "10",
    "REPLY_BURST_MAX": "2",
    "REPLY_BURST_SECONDS": "120",
    "REPLY_GH_TIMEOUT": "20",
    "REPLY_GH_OWNER": "",
}
# names the scripts have always read; they win over the generic factory.env names
ALIASES = {
    "BEEPER_BASE": ("BEEPER_API_BASE",),
    "TOKEN_FILE": ("BEEPER_TOKEN_FILE",),
    "CHAT_ID": ("ASK_ME_CHAT", "FACTORY_CHAT"),
}
PATHS = ("TOKEN_FILE", "WORK_DIR", "ASK_ME_STATE")


def env_file():
    return Path(os.environ.get("FACTORY_ENV") or KIT / "factory.env")


def parse(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not m:
            continue
        v = m.group(2)
        if v[:1] in "'\"":
            q = v[0]
            end = v.find(q, 1)
            v = v[1:end] if end > 0 else v[1:]
        else:
            v = re.sub(r"\s+#.*$", "", v)
        out[m.group(1)] = v
    return out


def expand(v):
    home = os.environ.get("HOME") or os.path.expanduser("~")
    v = re.sub(r"\$\{HOME\}|\$HOME", lambda _: home, v)
    if v == "~" or v.startswith("~/"):
        v = home + v[1:]
    return v


def load():
    path = env_file()
    try:
        file_values = parse(path.read_text())
    except OSError:
        file_values = {}
    cfg = {}
    for key, default in DEFAULTS.items():
        value = None
        for name in ALIASES.get(key, ()) + (key,):
            if os.environ.get(name):
                value = os.environ[name]
                break
        if value is None:
            value = file_values.get(key) or default
        cfg[key] = expand(value).strip()
    for key in PATHS:
        if cfg[key]:
            cfg[key] = os.path.abspath(cfg[key])
    dirs = [os.path.abspath(expand(p.strip())) for p in cfg["PROTECTED_DIRS"].split(":") if p.strip()]
    if str(KIT) not in dirs:
        dirs.append(str(KIT))  # the kit holds the guards and the watchdog pin: the factory never edits it
    cfg["PROTECTED_DIRS"] = dirs
    cfg["KIT"] = str(KIT)
    cfg["ENV_FILE"] = str(path)
    cfg["ENV_FILE_EXISTS"] = path.is_file()
    return cfg


def problems(cfg):
    path = cfg["ENV_FILE"]
    if not cfg["ENV_FILE_EXISTS"] and not all(cfg[k] for k in REQUIRED):
        return [f"no {path}: copy {KIT / 'factory.env.example'} to {path} and fill it in"]
    return [f"{k} is not set: add {k}=... to {path}" for k in REQUIRED if not cfg[k]]


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "json"
    cfg = load()
    if cmd == "json":
        print(json.dumps({k: v for k, v in cfg.items() if k != "ENV_FILE_EXISTS"}, indent=1))
    elif cmd == "get" and len(argv) == 3:
        v = cfg[argv[2]]
        print(":".join(v) if isinstance(v, list) else v)
    elif cmd == "check":
        p = problems(cfg)
        for line in p:
            print("factory-kit: " + line, file=sys.stderr)
        return 2 if p else 0
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
