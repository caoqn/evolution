#!/usr/bin/env python3
"""Export per-case LoCoBench runner durations from a completed run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def latest_event(events: list[dict], event_type: str) -> dict | None:
    return next((event for event in reversed(events) if event.get("type") == event_type), None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    output_path = run_dir / "durations.json"
    if output_path.exists():
        raise SystemExit(f"Refusing to overwrite existing {output_path}")

    records: list[dict] = []
    for events_path in sorted((run_dir / "cases").glob("*/events.jsonl")):
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        start = latest_event(events, "runner.start")
        end = latest_event(events, "runner.end")
        timeout = latest_event(events, "runner.timeout")
        result_path = events_path.parent / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        records.append({
            "case_dir": events_path.parent.name,
            "scenario_id": result.get("scenario_id"),
            "title": result.get("title"),
            "started_at": start.get("ts") if start else None,
            "ended_at": end.get("ts") if end else None,
            "elapsed_seconds": (end or {}).get("data", {}).get("cost_seconds"),
            "timed_out": timeout is not None,
            "result_written": result_path.exists(),
            "score": result.get("score"),
        })

    output_path.write_text(
        json.dumps({"run_dir": str(run_dir), "cases": records}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output_path} ({len(records)} cases)")


if __name__ == "__main__":
    main()
