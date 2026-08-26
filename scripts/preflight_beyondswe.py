#!/usr/bin/env python3
"""Validate local BeyondSWE prerequisites without making LLM calls."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from benchmarks.adapter_beyondswe import (  # noqa: E402
    SPLIT_MAP,
    SUPPORTED_TASK_TYPES,
    get_beyondswe_data_file,
    _normalize_task_type,
)


def _data_file() -> Path:
    return get_beyondswe_data_file()


def _check_data(path: Path, split: str) -> tuple[bool, int]:
    if not path.exists():
        print(f"[missing] Dataset JSONL: {path}")
        print("Download the official Standard Format dataset:")
        print(
            "  .venv311/bin/python -m pip install huggingface_hub\n"
            "  .venv311/bin/python -c 'from huggingface_hub import "
            "snapshot_download; snapshot_download(repo_id=\"AweAI-Team/BeyondSWE\", "
            "repo_type=\"dataset\", local_dir=\"benchmarks/BeyondSWE\")'"
        )
        print(
            "Then set BEYONDSWE_DATA_FILE to the downloaded JSONL if it is "
            "not benchmarks/BeyondSWE/data/beyondswe.jsonl."
        )
        return False, 0

    required = {
        "instance_id", "task", "problem_statement", "image_url", "workdir",
        "FAIL_TO_PASS", "PASS_TO_PASS",
    }
    target_types = SPLIT_MAP[split]
    count = 0
    seen_ids: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[invalid] JSONL line {line_no}: {exc}")
                return False, count
            task_type = _normalize_task_type(str(item.get("task", "")))
            if task_type not in SUPPORTED_TASK_TYPES:
                continue
            if target_types is not None and task_type not in target_types:
                continue
            missing = sorted(key for key in required if not item.get(key))
            if missing:
                print(f"[invalid] {item.get('instance_id', line_no)} missing: {', '.join(missing)}")
                return False, count
            instance_id = str(item["instance_id"])
            if instance_id in seen_ids:
                print(f"[invalid] Duplicate instance_id: {instance_id}")
                return False, count
            seen_ids.add(instance_id)
            for field in ("FAIL_TO_PASS", "PASS_TO_PASS"):
                value = item[field]
                try:
                    tests = json.loads(value) if isinstance(value, str) else value
                except json.JSONDecodeError as exc:
                    print(f"[invalid] {instance_id} {field}: {exc}")
                    return False, count
                if not isinstance(tests, list) or not tests:
                    print(f"[invalid] {instance_id} {field} must be a non-empty list")
                    return False, count
            count += 1
    if count == 0:
        print(f"[invalid] No usable {split} instances found in {path}")
        return False, 0
    print(f"[ok] Dataset: {path} ({count} usable {split} instances)")
    return True, count


def _check_docker() -> bool:
    docker = shutil.which("docker")
    if not docker:
        print("[missing] Docker CLI. Install/start Docker Desktop before BeyondSWE runs.")
        return False
    result = subprocess.run(
        [docker, "info", "--format", "{{.ServerVersion}}"],
        text=True, capture_output=True,
    )
    if result.returncode:
        print("[unavailable] Docker daemon is not reachable:", result.stderr.strip())
        return False
    print(f"[ok] Docker daemon: {result.stdout.strip()}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=list(SPLIT_MAP), default="crossrepo")
    parser.add_argument(
        "--skip-docker", action="store_true",
        help="Validate only dataset structure (useful before Docker is installed)",
    )
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        print(f"[invalid] Python 3.10+ required; found {sys.version.split()[0]}")
        raise SystemExit(2)
    print(f"[ok] Python: {sys.version.split()[0]}")
    data_ok, _ = _check_data(_data_file(), args.split)
    docker_ok = True if args.skip_docker else _check_docker()
    if not (data_ok and docker_ok):
        raise SystemExit(2)
    print("[ready] BeyondSWE preflight passed; no model calls were made.")


if __name__ == "__main__":
    main()
