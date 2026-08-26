#!/usr/bin/env python3
"""Import selected GAIA run directories into the canonical result manifest.

Run directories are processed in the order supplied. Use this only after
choosing the intended provenance order; it deliberately does not scan every
historical experiment automatically.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from benchmarks.adapter_gaia import load_gaia_data
from core.result_manifest import ResultManifest


def _looks_infrastructure_failure(record: dict) -> bool:
    if record.get("infrastructure_failure"):
        return True
    text = str(record.get("run_error", "")).lower()
    markers = ("api_failure", "badgateway", "502", "503", "rate limit", "api connection")
    return any(marker in text for marker in markers)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="append", required=True, help="Run ID under runs/; repeat in intended chronology")
    parser.add_argument(
        "--manifest", type=Path,
        default=BASE_DIR / "runs" / "manifests" / "gaia_test_100.json",
    )
    parser.add_argument(
        "--replace-valid", action="store_true",
        help="Force each imported record to replace an existing usable result",
    )
    args = parser.parse_args()

    task_to_index = {
        item["task_id"]: index
        for index, item in enumerate(load_gaia_data("test_100"))
    }
    manifest = ResultManifest(args.manifest, "gaia", "test_100")
    imported = skipped = 0

    for run_id in args.run:
        run_dir = BASE_DIR / "runs" / run_id
        if not run_dir.is_dir():
            raise FileNotFoundError(f"Run not found: {run_dir}")
        for result_path in sorted(run_dir.glob("cases/*/result.json")):
            record = json.loads(result_path.read_text(encoding="utf-8"))
            task_id = record.get("task_id", "")
            original_index = task_to_index.get(task_id)
            if original_index is None:
                skipped += 1
                print(f"[skip] {run_id}: unknown task_id {task_id!r}")
                continue
            record["infrastructure_failure"] = _looks_infrastructure_failure(record)
            record["_result_path"] = str(result_path)
            manifest.record_attempt(
                record,
                run_id=run_id,
                original_index=original_index,
                force_replace_valid=args.replace_valid,
            )
            imported += 1

    summary = manifest.summary()
    print(f"Imported {imported} result(s), skipped {skipped}.")
    print(
        f"Effective {summary['effective_records']} | correct {summary['correct']} | "
        f"accuracy {summary['accuracy']:.1%} | API pending {summary['infrastructure_pending']}"
    )


if __name__ == "__main__":
    main()
