"""R7 polite fetching: a tool User-Agent, a 2-second pause between requests, one fetch per URL per run, and
every raw body kept with its sha256 outside the repo."""
import hashlib

from conftest import fetcher_for

from eringi_price.config import Config
from eringi_price.fetch import Fetcher


def test_user_agent_names_the_tool_and_has_no_personal_data(config):
    ua = config.user_agent
    assert ua.startswith("eringi-price/")
    assert "@" not in ua and "gmail" not in ua.lower()


def test_pause_between_requests_and_once_per_url(tmp_path):
    cfg = Config(data_dir=tmp_path, pause=2.0)
    sleeps = []
    f, t = fetcher_for(cfg, {"https://a/1": (200, b"x"), "https://a/2": (200, b"y")}, sleeps)
    f.get("https://a/1")
    f.get("https://a/2")
    again = f.get("https://a/1")
    assert [u for u, _ in t.calls] == ["https://a/1", "https://a/2"]
    assert sleeps == [2.0]
    assert again.body == b"x"
    assert t.calls[0][1]["User-Agent"] == cfg.user_agent


def test_raw_kept_with_sha256(config):
    f, _ = fetcher_for(config, {"https://a/y202609meisai_20261015": (200, b"PK..")})
    r = f.get("https://a/y202609meisai_20261015")
    assert r.ok and r.status == 200
    assert r.sha256 == hashlib.sha256(b"PK..").hexdigest()
    assert r.path.read_bytes() == b"PK.."
    assert str(config.data_dir) in str(r.path)


def test_errors_are_results_not_exceptions(config):
    f, _ = fetcher_for(config, {"https://a/503": (503, b"err"), "https://a/net": OSError("timeout")})
    r = f.get("https://a/503")
    assert not r.ok and r.status == 503 and r.path is not None  # the error page is kept too
    n = f.get("https://a/net")
    assert n.status is None and "timeout" in n.error
    m = f.get("https://a/missing")
    assert m.status == 404


def test_default_data_dir_is_outside_any_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ERINGI_DATA_DIR", raising=False)
    cfg = Config.from_env()
    assert cfg.data_dir == tmp_path / ".local" / "share" / "eringi-price"
    assert cfg.pause == 2.0


def test_settings_come_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ERINGI_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("ERINGI_ITEM", "しめじ")
    monkeypatch.setenv("ERINGI_SEND_TIME", "18:15")
    cfg = Config.from_env()
    assert cfg.data_dir == tmp_path / "d" and cfg.item == "しめじ" and (cfg.send_hour, cfg.send_minute) == (18, 15)


def test_settings_also_come_from_factory_env_and_real_env_wins(monkeypatch, tmp_path):
    f = tmp_path / "factory.env"
    f.write_text('OWNER_NAME="x"\nERINGI_ITEM="しめじ"\nERINGI_SEND_TIME="18:30"\n', encoding="utf-8")
    monkeypatch.setenv("FACTORY_ENV", str(f))
    monkeypatch.setenv("ERINGI_SEND_TIME", "17:05")
    monkeypatch.delenv("ERINGI_ITEM", raising=False)
    cfg = Config.from_env()
    assert cfg.item == "しめじ" and (cfg.send_hour, cfg.send_minute) == (17, 5)
    assert "OWNER_NAME" not in cfg.env


def test_fetcher_pause_zero_does_not_sleep(tmp_path):
    sleeps = []
    f = Fetcher(tmp_path, user_agent="eringi-price/t", pause=0, transport=lambda u, h: (200, b""),
                sleep=sleeps.append)
    f.get("https://a/1")
    f.get("https://a/2")
    assert sleeps == []
