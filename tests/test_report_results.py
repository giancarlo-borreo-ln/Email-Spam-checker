"""Tests for report_results.py."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import report_results


def write_rows(rows: list[dict]) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    )
    fields = ["truth", "predicted", "confidence", "status", "from", "subject", "reason"]
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fields})
    handle.close()
    return Path(handle.name)


class SummarizeTests(unittest.TestCase):
    def test_confusion_metrics_and_miss_rate(self):
        path = write_rows([
            {"truth": "spam", "predicted": "spam"},
            {"truth": "spam", "predicted": "ham"},
            {"truth": "ham", "predicted": "spam"},
            {"truth": "ham", "predicted": "ham"},
            {"truth": "spam", "predicted": "error"},
        ])
        self.addCleanup(path.unlink)

        row = report_results.summarize(path)
        self.assertEqual(row["rows"], 5)
        self.assertEqual(row["evaluated"], 4)
        self.assertEqual(row["errors"], 1)
        self.assertEqual((row["tp"], row["fp"], row["tn"], row["fn"]), (1, 1, 1, 1))
        self.assertEqual(row["accuracy"], 0.5)
        self.assertEqual(row["precision"], 0.5)
        self.assertEqual(row["recall"], 0.5)
        self.assertEqual(row["f1"], 0.5)
        self.assertEqual(row["fpr"], 0.5)
        self.assertEqual(row["miss"], 0.5)

    def test_perfect_run(self):
        path = write_rows([
            {"truth": "spam", "predicted": "spam"},
            {"truth": "ham", "predicted": "ham"},
        ])
        self.addCleanup(path.unlink)

        row = report_results.summarize(path)
        self.assertEqual(row["accuracy"], 1.0)
        self.assertEqual(row["miss"], 0.0)
        self.assertEqual(row["fpr"], 0.0)
        self.assertEqual(row["errors"], 0)

    def test_partial_csv_is_accepted(self):
        path = write_rows([
            {"truth": "spam", "predicted": "spam"},
            {"truth": "ham", "predicted": "ham"},
            {"truth": "spam", "predicted": "spam"},
        ])
        self.addCleanup(path.unlink)
        row = report_results.summarize(path)
        self.assertEqual(row["rows"], 3)
        self.assertEqual(row["evaluated"], 3)


if __name__ == "__main__":
    unittest.main()
