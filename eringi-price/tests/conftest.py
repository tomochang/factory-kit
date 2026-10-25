"""Shared helpers. Every network call in the tests goes through FakeTransport: nothing leaves the machine and
nothing is sent (ask-me is a stub or --dry-run)."""
import gzip
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures"
JST = timezone(timedelta(hours=9))

from eringi_price import calendar as cal  # noqa: E402
from eringi_price import osaka, tokyo_daily, tokyo_monthly  # noqa: E402
from eringi_price.config import Config  # noqa: E402
from eringi_price.fetch import Fetcher  # noqa: E402
from eringi_price.store import Store  # noqa: E402

BASE = "https://www.shijou.metro.tokyo.lg.jp/documents/d/shijou/"
REAL_MONTHS = {  # real 明細 files saved 2026-10-25 (sha256 in REFERENCE/README)
    "202509": "y202509meisai_20251017",
    "202608": "y202608meisai_20260914",
    "202609": "y202609meisai_20261015",
}


def real(rel):
    p = FIX / rel
    data = p.read_bytes()
    return gzip.decompress(data) if p.suffix == ".gz" else data


def xlsx(ym):
    return real("tokyo/%s.xlsx" % REAL_MONTHS[ym])


class FakeTransport:
    """url -> (status, body) or an Exception instance. Unknown URLs give 404. Records every call."""

    def __init__(self, world):
        self.world = dict(world)
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        got = self.world.get(url, (404, b""))
        if isinstance(got, Exception):
            raise got
        return got


def world_2026_10_25(**over):
    """The real sources as seen on 2026-10-25 (Sunday, 休市; Osaka 503)."""
    w = {
        tokyo_monthly.PAGE_URL: (200, real("tokyo/geppo-2026-10-25.html.gz")),
        cal.DATES_URL: (200, real("maff/dates-2026-10-25.html")),
        cal.CLOSED_URL: (200, real("maff/kyushi-2026-10-25.html")),
        tokyo_daily.URL: (200, real("tokyo_daily/sei_k2-2026-10-23.csv")),
        osaka.URL: (503, real("osaka/sikyo-503-2026-10-25.html")),
    }
    for ym, name in REAL_MONTHS.items():
        w[BASE + name] = (200, xlsx(ym))
    w.update(over)
    return w


@pytest.fixture(autouse=True)
def no_real_settings(monkeypatch, tmp_path):
    """A real factory.env or ERINGI_* variable on this Mac never reaches a test."""
    monkeypatch.setenv("FACTORY_ENV", str(tmp_path / "no-factory.env"))
    for k in [k for k in os.environ if k.startswith("ERINGI_")]:
        monkeypatch.delenv(k)


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=tmp_path / "data", ask_me=str(tmp_path / "no-ask-me.py"), pause=0.0)


@pytest.fixture
def store(config):
    return Store(config.data_dir)


def seed_months(store, yms):
    for ym in yms:
        store.put_tokyo(ym, BASE + REAL_MONTHS[ym], xlsx(ym))
    return store


def fetcher_for(config, world, sleeps=None):
    t = FakeTransport(world)
    f = Fetcher(config.data_dir / "raw", user_agent=config.user_agent, pause=config.pause,
                transport=t, sleep=(sleeps.append if sleeps is not None else (lambda s: None)))
    return f, t


def at(y, m, d, hh=17, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=JST)


@pytest.fixture
def day():
    return date(2026, 10, 25)
