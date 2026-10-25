"""Source states (R5). Only NEW shows prices as today's; every other state is said on line 1 (or, for a monthly
source with no new month, shown with its month)."""
from dataclasses import dataclass, field
from typing import Any, List, Optional

NEW = "new"                         # new data for today (or a new month)
CLOSED = "closed"                   # 休市: not an open day by the MAFF calendar
NOT_YET = "not_yet"                 # not yet published (daily) / no new month yet (monthly)
FAILED = "failed"                   # fetch failed or format changed (503, empty, header changed, row missing)
AWAITING_FORMAT = "awaiting_format"  # a kind of FAILED: the source answers but its format is not recorded yet

STATES = (NEW, CLOSED, NOT_YET, FAILED, AWAITING_FORMAT)


class SourceError(Exception):
    reason = "取得失敗"

    def __init__(self, detail=""):
        super().__init__(detail or self.reason)
        self.detail = detail


class Empty(SourceError):
    reason = "取得失敗(空ファイル)"


class FormatChanged(SourceError):
    reason = "形式変更"


class NoItemRows(SourceError):
    reason = "エリンギ行なし"


class AwaitingFormat(SourceError):
    reason = "再開、形式確認待ち"


def fetch_reason(r):
    """Reason text for a fetch that did not return 200."""
    if r.status is None:
        return "取得失敗(接続)"
    return "取得失敗(%d)" % r.status


@dataclass
class SourceResult:
    source: str
    state: str
    reason: str = ""
    as_of: Optional[str] = None
    data: Any = None
    raw: List[dict] = field(default_factory=list)

    def to_json(self):
        return {"state": self.state, "reason": self.reason, "as_of": self.as_of, "raw": self.raw}


def raw_of(*results):
    return [{"url": r.url, "status": r.status, "sha256": r.sha256, "path": str(r.path) if r.path else None}
            for r in results if r is not None]
