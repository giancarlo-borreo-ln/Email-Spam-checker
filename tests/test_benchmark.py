"""Tests for the synthetic benchmark generator and metrics."""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import benchmark_spam as bench


class DatasetTests(unittest.TestCase):
    def test_dataset_sizes_labels_and_ids(self):
        samples = bench.make_dataset(60, 20, seed=7)
        self.assertEqual(len(samples), 80)
        self.assertEqual(sum(1 for sample in samples if sample.label == "spam"), 60)
        self.assertEqual(sum(1 for sample in samples if sample.label == "ham"), 20)
        ids = [sample.meta.message_id for sample in samples]
        self.assertEqual(len(ids), len(set(ids)))
        for sample in samples:
            self.assertTrue(sample.meta.from_)
            self.assertTrue(sample.meta.subject)
            self.assertTrue(sample.meta.snippet)
            self.assertTrue(sample.meta.uid)

    def test_hard_dataset_is_labeled_and_uses_mixed_auth(self):
        from hard_examples import make_hard_dataset

        samples = make_hard_dataset(60, 60, seed=11)
        self.assertEqual(len(samples), 120)
        self.assertEqual(sum(1 for sample in samples if sample.label == "spam"), 60)
        self.assertEqual(sum(1 for sample in samples if sample.label == "ham"), 60)
        self.assertEqual(len({s.meta.message_id for s in samples}), 120)
        # Hard spam includes pass-authentication cases; hard ham includes
        # forwarded mail that fails authentication.
        self.assertTrue(any(
            s.label == "spam" and "spf=pass" in s.meta.auth_results
            for s in samples
        ))
        self.assertTrue(any(
            s.label == "ham" and "spf=fail" in s.meta.auth_results
            for s in samples
        ))
        for sample in samples:
            self.assertTrue(sample.meta.from_)
            self.assertTrue(sample.meta.subject)
            self.assertTrue(sample.meta.snippet)

    def test_generator_is_deterministic(self):
        first = bench.make_dataset(20, 10, seed=3)
        second = bench.make_dataset(20, 10, seed=3)
        self.assertEqual(
            [(sample.label, sample.meta.from_, sample.meta.subject) for sample in first],
            [(sample.label, sample.meta.from_, sample.meta.subject) for sample in second],
        )

    def test_write_dataset(self):
        samples = bench.make_dataset(5, 5, seed=1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "emails.csv"
            bench.write_dataset(path, samples)
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 10)
        self.assertEqual(set(rows[0]), {"label", "from", "reply_to", "subject", "auth_results", "snippet"})
        self.assertEqual({row["label"] for row in rows}, {"spam", "ham"})


class MetricsTests(unittest.TestCase):
    def test_confusion_metrics(self):
        # tp=2, fp=1, tn=1, fn=0
        samples = [
            bench.Sample("spam", bench.EmailMeta(from_="s1", subject="s1")),
            bench.Sample("spam", bench.EmailMeta(from_="s2", subject="s2")),
            bench.Sample("ham", bench.EmailMeta(from_="h1", subject="h1")),
            bench.Sample("ham", bench.EmailMeta(from_="h2", subject="h2")),
        ]
        results = [
            (samples[0], "spam", 0.99, "x", "ok", 10, 1.0),
            (samples[1], "spam", 0.99, "x", "ok", 10, 1.0),
            (samples[2], "spam", 0.99, "x", "ok", 10, 1.0),
            (samples[3], "ham", 0.99, "x", "ok", 10, 1.0),
        ]
        out = io.StringIO()
        with redirect_stdout(out):
            ok = bench.print_metrics(results)
        text = out.getvalue()

        self.assertFalse(ok)  # FPR is 0.5, above the 0.10 threshold
        self.assertIn("Accuracy:   0.750", text)
        self.assertIn("Precision:  0.667", text)
        self.assertIn("Recall:     1.000", text)
        self.assertIn("False positive rate: 0.500", text)
        self.assertIn("False positives", text)

    def test_perfect_run_passes(self):
        samples = [
            bench.Sample("spam", bench.EmailMeta(from_="s", subject="s")),
            bench.Sample("ham", bench.EmailMeta(from_="h", subject="h")),
        ]
        results = [
            (samples[0], "spam", 0.99, "x", "ok", 10, 1.0),
            (samples[1], "ham", 0.99, "x", "ok", 10, 1.0),
        ]
        with redirect_stdout(io.StringIO()):
            ok = bench.print_metrics(results)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
