"""Settings. Every value can come from the environment (ERINGI_*); factory.env in the kit and the LaunchAgent
set them. Defaults keep raw data outside any repo (R7)."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__


KIT = Path(__file__).resolve().parents[2]  # <kit>/eringi-price/eringi_price -> <kit>


def _home():
    return Path(os.environ.get("HOME") or os.path.expanduser("~"))


def _env_file_values(env):
    """ERINGI_* keys from the kit's factory.env (or FACTORY_ENV); the real environment wins over them."""
    path = Path(env.get("FACTORY_ENV") or KIT / "factory.env")
    out = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?(ERINGI_[A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m:
            v = m.group(2)
            if v[:1] in "'\"" and v.endswith(v[0]) and len(v) >= 2:
                v = v[1:-1]
            out[m.group(1)] = v
    return out


def _path(v):
    v = str(v)
    if v == "~" or v.startswith("~/"):
        v = str(_home()) + v[1:]
    return Path(v.replace("$HOME", str(_home())))


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: _home() / ".local" / "share" / "eringi-price")
    ask_me: str = field(default_factory=lambda: str(KIT / "scripts" / "ask-me.py"))  # set ERINGI_ASK_ME elsewhere
    item: str = "エリンギ"
    item_code: str = "386300"
    user_agent: str = "eringi-price/%s (private daily price report; 1 request at a time)" % __version__
    pause: float = 2.0
    backfill_from: str = "202401"
    send_hour: int = 17
    send_minute: int = 0
    deadline: str = "17:30"
    label: str = "com.factory.eringi-price"
    sender: str = "eringi-price"
    canary_weekday: int = 0  # Monday: the daily run also runs the canary
    env: dict = field(default_factory=dict)

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        env = dict(_env_file_values(env), **{k: v for k, v in env.items() if v != ""})
        c = cls()
        g = env.get
        if g("ERINGI_DATA_DIR"):
            c.data_dir = _path(g("ERINGI_DATA_DIR"))
        if g("ERINGI_ASK_ME"):
            c.ask_me = str(_path(g("ERINGI_ASK_ME")))
        c.item = g("ERINGI_ITEM") or c.item
        c.item_code = g("ERINGI_ITEM_CODE") or c.item_code
        c.user_agent = g("ERINGI_USER_AGENT") or c.user_agent
        if g("ERINGI_PAUSE"):
            c.pause = max(float(g("ERINGI_PAUSE")), 1.0)
        c.backfill_from = (g("ERINGI_BACKFILL_FROM") or c.backfill_from).replace("-", "")
        if g("ERINGI_SEND_TIME"):
            h, m = g("ERINGI_SEND_TIME").split(":")
            c.send_hour, c.send_minute = int(h), int(m)
        c.deadline = g("ERINGI_DEADLINE") or c.deadline
        c.label = g("ERINGI_LAUNCHD_LABEL") or c.label
        c.env = {k: v for k, v in env.items() if k.startswith("ERINGI_")}
        return c
