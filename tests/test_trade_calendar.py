import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import trade_calendar as calendar


class TradingCalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "trade_calendar.json"
        entry = calendar.build_year(
            2026,
            ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07"],
            source="test",
            source_url="https://example.invalid/calendar",
            source_response_hash="sha256:test",
            fetched_at="2026-01-01T00:00:00+08:00",
        )
        self.path.write_text(json.dumps({
            "schema_version": calendar.SCHEMA_VERSION,
            "calendar_id": "test-calendar",
            "years": {"2026": entry},
        }), encoding="utf-8")
        self.patch = patch.object(calendar, "CACHE_PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_reads_complete_daily_state(self):
        self.assertTrue(calendar.is_trading_day("2026-10-09"))
        self.assertFalse(calendar.is_trading_day("2026-10-10"))
        self.assertFalse(calendar.is_trading_day("2026-10-05"))

    def test_uncovered_year_fails_instead_of_guessing(self):
        with self.assertRaises(calendar.CalendarCoverageError):
            calendar.is_trading_day("2027-01-04")

    def test_incomplete_year_fails_validation(self):
        value = json.loads(self.path.read_text(encoding="utf-8"))
        value["years"]["2026"]["days"].pop()
        self.path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(calendar.CalendarCoverageError, "不完整"):
            calendar.is_trading_day("2026-10-09")


if __name__ == "__main__":
    unittest.main()
