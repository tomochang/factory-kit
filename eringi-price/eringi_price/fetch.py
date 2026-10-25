"""Polite fetching (R7): one request at a time, a pause between requests, a User-Agent naming the tool, at most
one fetch per URL per run, and every body kept with its sha256 under the data dir."""
import hashlib
import http.cookiejar
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

JST = timezone(timedelta(hours=9))


@dataclass
class FetchResult:
    url: str
    status: Optional[int]
    body: bytes = b""
    error: str = ""
    sha256: str = ""
    path: Optional[Path] = None

    @property
    def ok(self):
        return self.status == 200


def urllib_transport():
    jar = http.cookiejar.CookieJar()  # the MAFF 休市 popup belongs to the date page's session
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def transport(url, headers):
        req = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(req, timeout=45) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read() if hasattr(e, "read") else b""
    return transport


class Fetcher:
    def __init__(self, raw_dir, user_agent, pause=2.0, transport=None, sleep=time.sleep, clock=None):
        self.raw_dir = Path(raw_dir)
        self.user_agent = user_agent
        self.pause = pause
        self.transport = transport or urllib_transport()
        self.sleep = sleep
        self.clock = clock or (lambda: datetime.now(JST))
        self.cache = {}
        self.requests = 0

    def _keep(self, url, body):
        sha = hashlib.sha256(body).hexdigest()
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", url.rstrip("/").rsplit("/", 1)[-1])[:80] or "index"
        day = self.clock().strftime("%Y-%m-%d")
        path = self.raw_dir / day / ("%s-%s" % (name, sha[:12]))
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(body)
        return sha, path

    def get(self, url):
        if url in self.cache:
            return self.cache[url]
        if self.requests and self.pause:
            self.sleep(self.pause)
        self.requests += 1
        headers = {"User-Agent": self.user_agent, "Accept-Language": "ja"}
        try:
            status, body = self.transport(url, headers)
            body = body or b""
            sha, path = self._keep(url, body)
            res = FetchResult(url, status, body, "", sha, path)
        except Exception as e:  # network errors are results, never a "closed" day (REFERENCE items 1-3)
            res = FetchResult(url, None, b"", "%s: %s" % (type(e).__name__, e))
        self.cache[url] = res
        return res
