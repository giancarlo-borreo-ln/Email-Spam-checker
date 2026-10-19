#!/usr/bin/env python3
"""
report_results.py - score and compare spam-benchmark result CSVs.

Works on partial files too, because benchmark_spam.py flushes each row.

Usage:
    python report_results.py spam-benchmark-4b-results.csv
    python report_results.py spam-benchmark-4b-results.csv spam-benchmark-8b-results.csv

Metrics:
    miss %  = spam predicted as ham / all spam   (false negative rate)
    FPR %   = ham predicted as spam / all ham    (false positive rate)
    time    = wall-clock time from the matching *-meta.json file
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def meta_path_for(path: Path) -> Path:
    if path.name.endswith("-results.csv"):
        return path.with_name(path.name[: -len("-results.csv")] + "-meta.json")
    return path.with_suffix(".meta.json")


def summarize(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    tp = fp = tn = fn = 0
    errors = 0
    false_positives = []
    false_negatives = []

    for row in rows:
        truth = (row.get("truth") or "").strip().lower()
        pred = (row.get("predicted") or "").strip().lower()
        if pred not in ("spam", "ham"):
            errors += 1
            continue
        if truth == "spam" and pred == "spam":
            tp += 1
        elif truth == "spam" and pred == "ham":
            fn += 1
            false_negatives.append(row)
        elif truth == "ham" and pred == "spam":
            fp += 1
            false_positives.append(row)
        elif truth == "ham" and pred == "ham":
            tn += 1

    evaluated = tp + fp + tn + fn
    accuracy = (tp + tn) / evaluated if evaluated else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    miss = fn / (tp + fn) if tp + fn else 0.0

    meta = {}
    meta_path = meta_path_for(path)
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}

    latencies = []
    for row in rows:
        value = row.get("latency_ms")
        if value:
            try:
                latencies.append(float(value))
            except ValueError:
                pass
    avg_ms = float(meta.get("avg_latency_ms") or (sum(latencies) / len(latencies) if latencies else 0.0))
    p95_ms = float(meta.get("p95_latency_ms") or 0.0)
    elapsed = float(meta.get("elapsed_seconds") or 0.0)
    rate = float(meta.get("emails_per_second") or (len(rows) / elapsed if elapsed else 0.0))

    return {
        "path": path,
        "model": meta.get("model") or path.name.replace("-results.csv", ""),
        "rows": len(rows),
        "evaluated": evaluated,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "errors": errors,
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "fpr": fpr,
        "miss": miss,
        "elapsed": elapsed,
        "rate": rate,
        "avg_ms": avg_ms,
        "p95_ms": p95_ms,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }


def show(row: dict, label_width: int) -> None:
    def pct(value: float) -> str:
        return f"{value * 100:6.2f}%"

    minutes = f"{row['elapsed'] / 60:6.1f}m" if row["elapsed"] else "     -"
    rate = f"{row['rate']:5.2f}" if row["rate"] else "    -"
    avg = f"{row['avg_ms']:5.0f}" if row["avg_ms"] else "    -"

    print(f"{str(row['path']):<{label_width}} {row['rows']:>7} {row['evaluated']:>6} "
          f"{pct(row['accuracy'])} {pct(row['precision'])} {pct(row['recall'])} "
          f"{pct(row['miss'])} {pct(row['fpr'])} {row['f1']:.3f} "
          f"{minutes} {rate} {avg} {row['errors']:>6}")


def detail(row: dict, top: int) -> None:
    print("")
    print(f"{row['path']}  [{row['model']}]")
    print(f"  rows={row['rows']}  evaluated={row['evaluated']}  errors={row['errors']}")
    print(f"  TP={row['tp']}  FP={row['fp']}  TN={row['tn']}  FN={row['fn']}")
    print(f"  accuracy={row['accuracy']:.3f}  precision={row['precision']:.3f}  "
          f"recall={row['recall']:.3f}  F1={row['f1']:.3f}")
    print(f"  miss rate (spam -> ham)={row['miss'] * 100:.2f}%  "
          f"FPR (ham -> spam)={row['fpr'] * 100:.2f}%")
    if row["elapsed"]:
        print(f"  time={row['elapsed'] / 60:.1f} min  rate={row['rate']:.2f} emails/s  "
              f"avg={row['avg_ms']:.0f} ms  p95={row['p95_ms']:.0f} ms")

    if row["false_positives"]:
        print(f"\n  False positives (legitimate mail marked spam), showing {min(top, len(row['false_positives']))}:")
        for item in row["false_positives"][:top]:
            print(f"    - {item.get('from', '')} | {item.get('subject', '')}")
            print(f"      reason: {item.get('reason', '')}")
    if row["false_negatives"]:
        print(f"\n  False negatives (spam left as ham), showing {min(top, len(row['false_negatives']))}:")
        for item in row["false_negatives"][:top]:
            print(f"    - {item.get('from', '')} | {item.get('subject', '')}")
            print(f"      reason: {item.get('reason', '')}")


def compare(first: dict, second: dict) -> None:
    """Print a quality-vs-speed comparison. `second` is assumed to be the bigger model."""
    if not first["elapsed"] or not second["elapsed"]:
        print("\nTiming comparison needs *-meta.json files for both runs.")
        return

    ratio = second["elapsed"] / first["elapsed"]
    miss_gain = (first["miss"] - second["miss"]) * 100  # positive is better
    fpr_gain = (first["fpr"] - second["fpr"]) * 100     # positive is better
    acc_gain = (second["accuracy"] - first["accuracy"]) * 100

    print("")
    print("Comparison")
    print("==========")
    print(f"  {first['model']}: {first['elapsed'] / 60:.1f} min, "
          f"{first['rate']:.2f} emails/s, miss {first['miss'] * 100:.2f}%, "
          f"FPR {first['fpr'] * 100:.2f}%")
    print(f"  {second['model']}: {second['elapsed'] / 60:.1f} min, "
          f"{second['rate']:.2f} emails/s, miss {second['miss'] * 100:.2f}%, "
          f"FPR {second['fpr'] * 100:.2f}%")
    print(f"  time ratio: {ratio:.2f}x")
    print(f"  accuracy: {acc_gain:+.2f} pp   miss reduction: {miss_gain:+.2f} pp   "
          f"FPR reduction: {fpr_gain:+.2f} pp")

    meaningful = miss_gain >= 1.0 or fpr_gain >= 1.0
    if not meaningful:
        print("  Verdict: NOT worth it on this dataset — no meaningful quality gain.")
    elif ratio <= 2.0:
        print("  Verdict: worth it — better quality for less than 2x the time.")
    elif ratio <= 4.0:
        print("  Verdict: marginal — better quality, but 2–4x slower.")
    else:
        print("  Verdict: not worth it — quality gain does not justify the slowdown.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score and compare spam benchmark result CSVs.")
    parser.add_argument("files", nargs="+", help="result CSV file(s)")
    parser.add_argument("--top", type=int, default=10, help="errors to show per file")
    parser.add_argument("--detail", action="store_true", help="show per-file details")
    args = parser.parse_args(argv)

    rows = []
    for name in args.files:
        path = Path(name)
        if not path.is_file():
            print(f"missing: {path}", file=sys.stderr)
            return 2
        rows.append(summarize(path))

    if not rows:
        return 2

    label_width = max(len(str(row["path"])) for row in rows)
    label_width = max(label_width, 12)
    header = (
        f"{'file':<{label_width}} {'rows':>7} {'eval':>6} "
        f"{'acc':>7} {'prec':>7} {'recall':>7} {'miss':>7} {'fpr':>7} "
        f"{'F1':>5} {'time':>7} {'em/s':>5} {'ms':>5} {'errors':>6}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        show(row, label_width)

    if args.detail or len(rows) > 1:
        for row in rows:
            detail(row, args.top)

    if len(rows) == 2:
        compare(rows[0], rows[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
