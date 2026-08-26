#!/usr/bin/env python3
"""Print the current effective outcomes in a benchmark result manifest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.result_manifest import ResultManifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    # Metadata is read by ResultManifest; these placeholders are only used if
    # the requested file does not exist yet.
    manifest = ResultManifest(args.manifest, benchmark="unknown", split="unknown")
    summary = manifest.summary()
    print(f"Manifest: {args.manifest}")
    print(
        f"Effective: {summary['effective_records']} | "
        f"Scored: {summary['scored_records']} | "
        f"Correct: {summary['correct']} | "
        f"Accuracy: {summary['accuracy']:.1%} | "
        f"API pending: {summary['infrastructure_pending']}"
    )
    if summary["infrastructure_pending_indices"]:
        print("API pending indices:", ", ".join(map(str, summary["infrastructure_pending_indices"])))
    print("\nindex\tstatus\tanswer\trun")
    for record in manifest.effective_records():
        status = (
            "API_PENDING" if record.get("infrastructure_failure")
            else "CORRECT" if record.get("is_correct")
            else "INCORRECT"
        )
        print(
            f"{record['original_index']}\t{status}\t"
            f"{record.get('extracted_answer', '')[:80]}\t{record.get('run_id', '')}"
        )


if __name__ == "__main__":
    main()
