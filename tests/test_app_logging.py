import datetime as dt
import logging
import os
import tempfile
import types
import unittest
from zoneinfo import ZoneInfo

from lib import app_logging
from lib.app_logging import ACTIVITY_LOGGER_NAME, DailyFileHandler, TZFormatter, log_activity, setup_logging

JST = ZoneInfo("Asia/Tokyo")


def record_at(when: dt.datetime, msg="x"):
    r = logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)
    r.created = when.timestamp()
    return r


class DailyFileHandlerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def files(self):
        return sorted(os.listdir(self.dir))

    def test_switches_file_on_date_change_in_timezone(self):
        h = DailyFileHandler(self.dir, retention_days=0, tz=JST)
        h.setFormatter(logging.Formatter("%(message)s"))
        # UTC 14:59 = JST 23:59 (11/26)、UTC 15:00 = JST 0:00 (11/27)
        h.emit(record_at(dt.datetime(2026, 11, 26, 14, 59, tzinfo=dt.timezone.utc), "a"))
        h.emit(record_at(dt.datetime(2026, 11, 26, 15, 0, tzinfo=dt.timezone.utc), "b"))
        h.close()
        self.assertEqual(self.files(), ["activity-2026-11-26.log", "activity-2026-11-27.log"])
        with open(os.path.join(self.dir, "activity-2026-11-27.log"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "b\n")

    def test_appends_to_existing_file(self):
        for msg in ("a", "b"):  # 再起動しても同じ日のファイルに追記する
            h = DailyFileHandler(self.dir, retention_days=0, tz=JST)
            h.setFormatter(logging.Formatter("%(message)s"))
            h.emit(record_at(dt.datetime(2026, 11, 27, 12, tzinfo=JST), msg))
            h.close()
        with open(os.path.join(self.dir, "activity-2026-11-27.log"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "a\nb\n")

    def test_deletes_files_older_than_retention(self):
        for name in ("activity-2026-10-27.log", "activity-2026-10-28.log", "other.log", "activity-bad.log"):
            open(os.path.join(self.dir, name), "w").close()
        h = DailyFileHandler(self.dir, retention_days=30, tz=JST)
        h.setFormatter(logging.Formatter("%(message)s"))
        h.emit(record_at(dt.datetime(2026, 11, 27, 12, tzinfo=JST)))
        h.close()
        # 30日前(10/28)までは残し、それより古いものだけ消す。無関係なファイルは触らない
        self.assertEqual(self.files(), ["activity-2026-10-28.log", "activity-2026-11-27.log", "activity-bad.log", "other.log"])


class FormatterTest(unittest.TestCase):
    def test_time_has_offset(self):
        f = TZFormatter("%(asctime)s %(message)s", JST)
        r = record_at(dt.datetime(2026, 11, 27, 3, 4, 5, tzinfo=dt.timezone.utc), "hi")
        self.assertEqual(f.format(r), "2026-11-27 12:04:05+09:00 hi")


class ActivityLogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.root_handlers = list(logging.getLogger().handlers)
        self.root_level = logging.getLogger().level

    def tearDown(self):
        activity = logging.getLogger(ACTIVITY_LOGGER_NAME)
        for h in list(activity.handlers):
            activity.removeHandler(h)
            h.close()
        root = logging.getLogger()
        for h in list(root.handlers):
            if h not in self.root_handlers:
                root.removeHandler(h)
        root.setLevel(self.root_level)
        app_logging._include_text = True

    def setup(self, **activity):
        setup_logging({"logging": {"activity_log": {"directory": self.dir, **activity}}})

    def lines(self):
        files = os.listdir(self.dir)
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0], r"^activity-\d{4}-\d{2}-\d{2}\.log$")
        with open(os.path.join(self.dir, files[0]), encoding="utf-8") as f:
            return f.read().splitlines()

    user = types.SimpleNamespace(id=9, name="masato", display_name="まさと")
    guild = types.SimpleNamespace(id=1, name="雑談鯖")
    channel = types.SimpleNamespace(id=2, name="読み上げ")

    def test_line_has_datetime_user_and_text(self):
        self.setup()
        log_activity("tts", user=self.user, guild=self.guild, channel=self.channel,
                     detail="speaker=3", text="こんにちは\n2行目\tタブ")
        (line,) = self.lines()
        fields = line.split("\t")
        self.assertRegex(fields[0], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+09:00$")
        self.assertEqual(fields[1:], ["tts", "雑談鯖(1)", "読み上げ(2)", "まさと(@masato, 9)", "speaker=3",
                                      "こんにちは\\n2行目\\tタブ"])

    def test_include_text_false_omits_text(self):
        self.setup(include_text=False)
        log_activity("tts", user=self.user, guild=self.guild, channel=self.channel, text="秘密")
        (line,) = self.lines()
        self.assertNotIn("秘密", line)
        self.assertTrue(line.endswith("まさと(@masato, 9)\t-"))

    def test_disabled_writes_nothing(self):
        self.setup(enabled=False)
        log_activity("tts", user=self.user, text="a")
        self.assertEqual(os.listdir(self.dir), [])

    def test_activity_not_on_stdout_and_root_is_info(self):
        self.setup()
        self.assertFalse(logging.getLogger(ACTIVITY_LOGGER_NAME).propagate)
        self.assertEqual(logging.getLogger().level, logging.INFO)
        # 2回呼んでもハンドラーが重複しない
        n = len(logging.getLogger().handlers)
        self.setup()
        self.assertEqual(len(logging.getLogger().handlers), n)
        self.assertEqual(len(logging.getLogger(ACTIVITY_LOGGER_NAME).handlers), 1)


try:
    from cogs.system.activity_log import _options
except ImportError:  # discord.py 未導入
    _options = None


@unittest.skipIf(_options is None, "discord.py が未インストール")
class CommandOptionsTest(unittest.TestCase):
    def test_subcommand_args(self):
        data = {"name": "dictionary", "options": [
            {"name": "add", "type": 1, "options": [
                {"name": "key", "type": 3, "value": "鯖"}, {"name": "value", "type": 3, "value": "さば"}]}]}
        self.assertEqual(_options(data), "key=鯖 value=さば")

    def test_no_args(self):
        self.assertEqual(_options({"name": "join"}), "")


if __name__ == "__main__":
    unittest.main()
