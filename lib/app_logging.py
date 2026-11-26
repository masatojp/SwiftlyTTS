"""ログの設定。

- 動作ログ: 標準出力に出す(docker compose logs で見られる)
- 操作ログ: 「いつ・誰が・何をしたか」を日付ごとのファイルに保存する
  (logs/activity-YYYY-MM-DD.log。保存期間を過ぎたファイルは自動で削除)

config.yml の logging セクションで設定する(config.yml.example を参照)。
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import sys
import threading
from typing import Any, Mapping, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

ACTIVITY_LOGGER_NAME = "swiftlytts.activity"

DEFAULTS = {
    "level": "INFO",
    "timezone": "Asia/Tokyo",
    "activity_log": {
        "enabled": True,
        "directory": "logs",
        "retention_days": 30,
        "include_text": True,
    },
}

_include_text = True


def _tz(name: Optional[str]):
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            logging.getLogger(__name__).warning(f"不明なタイムゾーン {name!r} のため、システムの時刻を使います")
    return None


class TZFormatter(logging.Formatter):
    """指定したタイムゾーンで「2026-11-27 12:34:56+09:00」の形式で時刻を出す。"""

    def __init__(self, fmt: str, tz=None):
        super().__init__(fmt)
        self.tz = tz

    def formatTime(self, record, datefmt=None):  # noqa: N802
        t = _dt.datetime.fromtimestamp(record.created, tz=self.tz) if self.tz else \
            _dt.datetime.fromtimestamp(record.created).astimezone()
        return t.isoformat(sep=" ", timespec="seconds")


class DailyFileHandler(logging.Handler):
    """日付ごとに別ファイル(<prefix>-YYYY-MM-DD.log)へ書き込む。

    日付が変わった最初の書き込みで新しいファイルを開き、retention_days より古いファイルを削除する。
    """

    def __init__(self, directory: str, prefix: str = "activity", retention_days: int = 30, tz=None):
        super().__init__()
        self.directory = directory
        self.prefix = prefix
        self.retention_days = retention_days
        self.tz = tz
        self._date: Optional[_dt.date] = None
        self._stream = None
        self._io_lock = threading.Lock()
        os.makedirs(directory, exist_ok=True)

    def _today(self, created: float) -> _dt.date:
        t = _dt.datetime.fromtimestamp(created, tz=self.tz) if self.tz else _dt.datetime.fromtimestamp(created)
        return t.date()

    def path_for(self, date: _dt.date) -> str:
        return os.path.join(self.directory, f"{self.prefix}-{date.isoformat()}.log")

    def _open_for(self, date: _dt.date) -> None:
        if self._stream is not None:
            self._stream.close()
        os.makedirs(self.directory, exist_ok=True)
        self._stream = open(self.path_for(date), "a", encoding="utf-8")
        self._date = date
        self._cleanup(date)

    def _cleanup(self, today: _dt.date) -> None:
        if not self.retention_days or self.retention_days <= 0:
            return
        pattern = re.compile(rf"^{re.escape(self.prefix)}-(\d{{4}}-\d{{2}}-\d{{2}})\.log$")
        cutoff = today - _dt.timedelta(days=self.retention_days)
        try:
            names = os.listdir(self.directory)
        except OSError:
            return
        for name in names:
            m = pattern.match(name)
            if not m:
                continue
            try:
                if _dt.date.fromisoformat(m.group(1)) < cutoff:
                    os.remove(os.path.join(self.directory, name))
            except (ValueError, OSError):
                pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._io_lock:
                date = self._today(record.created)
                if date != self._date or self._stream is None:
                    self._open_for(date)
                self._stream.write(msg + "\n")
                self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self._io_lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        super().close()


def _settings(config: Optional[Mapping[str, Any]]) -> dict:
    raw = dict((config or {}).get("logging") or {})
    activity = {**DEFAULTS["activity_log"], **(raw.get("activity_log") or {})}
    return {**DEFAULTS, **raw, "activity_log": activity}


def setup_logging(config: Optional[Mapping[str, Any]] = None) -> None:
    """動作ログ(標準出力)と操作ログ(日次ファイル)を設定する。起動時に1回呼ぶ。"""
    global _include_text
    s = _settings(config)
    tz = _tz(s.get("timezone"))
    level = os.getenv("LOG_LEVEL") or str(s.get("level") or "INFO")

    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_swiftlytts", False):
            root.removeHandler(h)
            h.close()
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(TZFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", tz))
    stdout._swiftlytts = True  # type: ignore[attr-defined]
    root.addHandler(stdout)
    try:
        root.setLevel(level.upper())
    except ValueError:
        root.setLevel(logging.INFO)

    activity = logging.getLogger(ACTIVITY_LOGGER_NAME)
    activity.propagate = False  # 標準出力には出さない(動作ログに同じ内容が出ているため)
    activity.setLevel(logging.INFO)
    for h in list(activity.handlers):
        activity.removeHandler(h)
        h.close()
    a = s["activity_log"]
    _include_text = bool(a.get("include_text", True))
    if a.get("enabled", True):
        try:
            handler = DailyFileHandler(str(a.get("directory") or "logs"), "activity",
                                       int(a.get("retention_days") or 0), tz)
        except OSError as e:
            logging.getLogger(__name__).error(f"操作ログのディレクトリを作成できません: {e}")
        else:
            handler.setFormatter(TZFormatter("%(asctime)s\t%(message)s", tz))
            activity.addHandler(handler)
            logging.getLogger(__name__).info(
                f"操作ログを {os.path.abspath(handler.directory)}/activity-YYYY-MM-DD.log に保存します"
                f"(保存期間 {handler.retention_days} 日)"
            )


def _clean(value: Any) -> str:
    # 1件1行にする(改行・タブを含む本文でも行が崩れないように)
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def _named(obj: Any) -> str:
    if obj is None:
        return "-"
    name = getattr(obj, "name", None) or "?"
    return f"{_clean(name)}({getattr(obj, 'id', '?')})"


def _user(user: Any) -> str:
    if user is None:
        return "-"
    display = getattr(user, "display_name", None) or getattr(user, "name", None) or "?"
    name = getattr(user, "name", None) or "?"
    return f"{_clean(display)}(@{_clean(name)}, {getattr(user, 'id', '?')})"


def log_activity(action: str, *, user: Any = None, guild: Any = None, channel: Any = None,
                 detail: str = "", text: Optional[str] = None) -> None:
    """操作ログに1行書く。形式(タブ区切り):

        日時  操作  サーバー名(ID)  チャンネル名(ID)  表示名(@ユーザー名, ID)  詳細  本文
    """
    logger = logging.getLogger(ACTIVITY_LOGGER_NAME)
    if not logger.handlers:
        return
    fields = [action, _named(guild), _named(channel), _user(user), _clean(detail) if detail else "-"]
    if text is not None and _include_text:
        fields.append(_clean(text))
    logger.info("\t".join(fields))
