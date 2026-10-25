"""Local state under the data dir (outside any repo): stored Tokyo months, Osaka rows, the run log and the
canary log."""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.tokyo_dir = self.root / "tokyo"
        self.runs_path = self.root / "runs.jsonl"
        self.canary_path = self.root / "canary.jsonl"
        self.osaka_path = self.root / "osaka.jsonl"
        self._months = {}

    # Tokyo months ------------------------------------------------------------------------------------------
    def tokyo_index(self):
        p = self.tokyo_dir / "index.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def has_tokyo(self, ym):
        return ym in self.tokyo_index()

    def tokyo_path(self, ym):
        return self.tokyo_dir / ("%s.xlsx" % ym)

    def put_tokyo(self, ym, url, body, at=None):
        from . import tokyo_monthly
        tokyo_monthly.parse_meisai(body)  # never store a file we cannot read
        self.tokyo_dir.mkdir(parents=True, exist_ok=True)
        self.tokyo_path(ym).write_bytes(body)
        idx = self.tokyo_index()
        idx[ym] = {"url": url, "file": self.tokyo_path(ym).name, "sha256": hashlib.sha256(body).hexdigest(),
                   "stored_at": (at or datetime.now(JST)).isoformat()}
        (self.tokyo_dir / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1, sort_keys=True),
                                                   encoding="utf-8")
        self._months.pop(ym, None)

    def verify_tokyo(self, ym):
        """sha256 of the stored file matches the index."""
        entry = self.tokyo_index().get(ym)
        p = self.tokyo_path(ym)
        return bool(entry) and p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == entry["sha256"]

    def tokyo_months(self):
        from . import tokyo_monthly
        out = {}
        for ym in self.tokyo_index():
            if ym not in self._months:
                self._months[ym] = tokyo_monthly.parse_meisai(self.tokyo_path(ym).read_bytes())
            out[ym] = self._months[ym]
        return out

    # Osaka rows (filled once the Osaka parser exists) --------------------------------------------------------
    def osaka_rows(self):
        from .osaka import Row
        if not self.osaka_path.exists():
            return []
        rows = []
        for line in self.osaka_path.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            d["date"] = date.fromisoformat(d["date"])
            rows.append(Row(**d))
        return rows

    def add_osaka_rows(self, rows):
        have = {(r.date, r.market, r.origin) for r in self.osaka_rows()}
        self.root.mkdir(parents=True, exist_ok=True)
        with self.osaka_path.open("a", encoding="utf-8") as f:
            for r in rows:
                if (r.date, r.market, r.origin) not in have:
                    d = dict(r.__dict__, date=r.date.isoformat())
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")

    # logs ----------------------------------------------------------------------------------------------------
    def _append(self, path, entry):
        self.root.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _read(self, path):
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def append_run(self, entry):
        self._append(self.runs_path, entry)

    def runs(self):
        return self._read(self.runs_path)

    def sent_on(self, day):
        return [r for r in self.runs() if r.get("day") == day.isoformat() and r.get("sent_at")]

    def append_canary(self, entry):
        self._append(self.canary_path, entry)

    def canary_runs(self):
        return self._read(self.canary_path)
