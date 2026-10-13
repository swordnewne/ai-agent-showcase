from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

WORKSPACE = Path(__file__).resolve().parents[2] if Path(__file__).resolve().parents[1].name == "showcase" else Path(__file__).resolve().parents[1]
FINANCIAL_PATH = WORKSPACE / "showcase" / "src" / "financial"
import sys
sys.path.insert(0, str(FINANCIAL_PATH))
import preopen_report
import preopen_runs


class NewsWindowTests(unittest.TestCase):
    def test_cutoff_is_persisted_and_evening_run_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(preopen_runs, "DB_PATH", Path(directory) / "runs.db"):
            preopen_runs.record_run("2026-10-09", "22:00", "complete", "evening.md", news_cutoff_at="2026-10-09T22:15:00+08:00")
            preopen_runs.record_run("2026-10-09", "08:15", "complete", "morning.md", news_cutoff_at="2026-10-09T08:18:00+08:00")
            self.assertEqual(
                preopen_runs.previous_news_cutoff("2026-10-12"),
                ("2026-10-09T08:18:00+08:00", "completed_preopen_run"),
            )

    def test_window_spans_weekend_and_uses_available_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            snapshots = {
                "2026-10-09": [self.row("fri-after-cutoff", "2026-10-09T08:16:00+08:00")],
                "2026-10-10": [self.row("sat", "2026-10-10T12:00:00+08:00")],
                "2026-10-11": [self.row("sun", "2026-10-11T12:00:00+08:00")],
                "2026-10-12": [self.row("mon", "2026-10-12T07:51:00+08:00")],
            }
            for day, rows in snapshots.items():
                (evidence / f"news_{day}.jsonl").write_text(
                    "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
                )
            with patch.object(preopen_report, "NEWS_EVIDENCE_DIR", evidence), \
                 patch.object(preopen_report, "previous_news_cutoff", return_value=("2026-10-09T08:15:00+08:00", "completed_preopen_run")), \
                 patch.object(preopen_report, "_collect_news_snapshot", return_value={"fetched_at": "2026-10-12T07:52:00+08:00", "status": "succeeded", "errors": []}):
                rows, window = preopen_report.fetch_news_window("2026-10-12", datetime.fromisoformat("2026-10-12T08:00:00+08:00"))
            self.assertEqual({row["title"] for row in rows}, {"fri-after-cutoff", "sat", "sun", "mon"})
            self.assertEqual(window["start_at"], "2026-10-09T08:15:00+08:00")
            self.assertEqual(window["cutoff_at"], "2026-10-12T07:52:00+08:00")

    def test_window_deduplicates_provider_item_and_does_not_invent_publish_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            duplicate = self.row("same", "2026-10-10T12:00:00+08:00")
            duplicate["source_item_id"] = "sina:42"
            duplicate["content_hash"] = "sha256:content"
            for day in ("2026-10-10", "2026-10-11"):
                (evidence / f"news_{day}.jsonl").write_text(json.dumps(duplicate) + "\n", encoding="utf-8")
            with patch.object(preopen_report, "NEWS_EVIDENCE_DIR", evidence), \
                 patch.object(preopen_report, "previous_news_cutoff", return_value=("2026-10-09T08:15:00+08:00", "completed_preopen_run")), \
                 patch.object(preopen_report, "_collect_news_snapshot", return_value=None):
                rows, _ = preopen_report.fetch_news_window(
                    "2026-10-12", datetime.fromisoformat("2026-10-12T08:00:00+08:00")
                )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].get("published_at"), None)
            self.assertFalse(rows[0].get("publication_time_verified", False))

    def test_window_keeps_a_changed_version_of_the_same_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            first = self.row("same", "2026-10-10T12:00:00+08:00")
            second = dict(first, intro="updated")
            second["content_hash"] = "sha256:updated"
            first["source_item_id"] = second["source_item_id"] = "sina:42"
            first["url"] = second["url"] = "https://example.invalid/news/42"
            for day, row in (("2026-10-10", first), ("2026-10-11", second)):
                (evidence / f"news_{day}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            with patch.object(preopen_report, "NEWS_EVIDENCE_DIR", evidence), \
                 patch.object(preopen_report, "previous_news_cutoff", return_value=("2026-10-09T08:15:00+08:00", "completed_preopen_run")), \
                 patch.object(preopen_report, "_collect_news_snapshot", return_value=None):
                rows, _ = preopen_report.fetch_news_window("2026-10-12", datetime.fromisoformat("2026-10-12T08:00:00+08:00"))
            self.assertEqual(len(rows), 2)

    @staticmethod
    def row(title: str, available_at: str) -> dict:
        return {
            "title": title,
            "source": "sina",
            "source_id": "sina",
            "source_item_id": f"sina:{title}",
            "content_hash": f"sha256:{title}",
            "available_at": available_at,
            "published_at_status": "missing",
            "published_at_raw": "",
            "publication_time_verified": False,
        }


if __name__ == "__main__":
    unittest.main()
