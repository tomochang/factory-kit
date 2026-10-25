"""The LaunchAgent (R9): runs `eringi-price run` daily at the send time (17:00 JST by default). Installing it is
the owner's step; tests pass a fake launchctl."""
import os
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- eringi-price: one エリンギ price report a day to his own chat (ask-me --tell). Rendered by `eringi-price install`. -->
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{program}</string>
    <string>run</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>{hour}</integer>
    <key>Minute</key><integer>{minute}</integer>
  </dict>
  <key>ProcessType</key><string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
{env}
  </dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def render_plist(config, program):
    env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "TZ": "Asia/Tokyo",
           "ERINGI_DATA_DIR": str(config.data_dir), "ERINGI_ASK_ME": config.ask_me,
           "ERINGI_SEND_TIME": "%02d:%02d" % (config.send_hour, config.send_minute),
           "ERINGI_LAUNCHD_LABEL": config.label}
    for k, v in sorted(config.env.items()):
        env.setdefault(k, v)
    body = "\n".join("    <key>%s</key><string>%s</string>" % (escape(k), escape(str(v))) for k, v in env.items())
    return TEMPLATE.format(label=escape(config.label), program=escape(str(program)), hour=config.send_hour,
                           minute=config.send_minute, env=body, log=escape(str(Path(config.data_dir) / "launchd.log")))


def _launchctl(*args):
    r = subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout, r.stderr


def install(config, program, agents=None, launchctl=None):
    agents = Path(agents or Path.home() / "Library" / "LaunchAgents")
    launchctl = launchctl or _launchctl
    agents.mkdir(parents=True, exist_ok=True)
    Path(config.data_dir).mkdir(parents=True, exist_ok=True)
    plist = agents / (config.label + ".plist")
    text = render_plist(config, program)
    if plist.exists() and plist.read_text(encoding="utf-8") != text:
        raise RuntimeError("%s exists with other content; run uninstall first" % plist)
    plist.write_text(text, encoding="utf-8")
    rc, out, err = launchctl("bootstrap", "gui/%d" % os.getuid(), str(plist))
    if rc != 0:
        raise RuntimeError("launchctl bootstrap failed: %s" % (err or out).strip())
    return plist


def uninstall(config, agents=None, launchctl=None):
    agents = Path(agents or Path.home() / "Library" / "LaunchAgents")
    launchctl = launchctl or _launchctl
    launchctl("bootout", "gui/%d/%s" % (os.getuid(), config.label))
    plist = agents / (config.label + ".plist")
    if plist.exists():
        plist.unlink()
    return plist
