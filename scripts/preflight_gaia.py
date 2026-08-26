#!/usr/bin/env python3
"""Fail fast when a GAIA Meta-Team run lacks required local resources."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "gaia"
FILES_DIR = DATA_DIR / "val_files"
REQUIRED_MODULES = (
    "aiohttp",
    "docx",
    "litellm",
    "openpyxl",
    "pandas",
    "pdfplumber",
    "pypdf",
    "pptx",
    "yaml",
)


def load_project_env() -> None:
    """Load the project-local experiment settings ahead of shell leftovers."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value[:value.index(" #")].strip()
        os.environ[key.strip()] = value


def configure_macos_proxy() -> None:
    """Inherit the active macOS HTTP proxy when the shell has none."""
    if any(os.environ.get(name) for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")):
        return
    try:
        output = subprocess.run(
            ["scutil", "--proxy"], check=True, capture_output=True, text=True, timeout=3
        ).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return
    host = re.search(r"^\s*HTTPProxy\s*:\s*(\S+)", output, re.MULTILINE)
    port = re.search(r"^\s*HTTPPort\s*:\s*(\d+)", output, re.MULTILINE)
    if not host or not port:
        return
    proxy = f"http://{host.group(1)}:{port.group(1)}"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ[name] = proxy


def check_python() -> list[str]:
    if sys.version_info >= (3, 10):
        return []
    return [f"Python 3.10+ is required; found {sys.version.split()[0]}."]


def check_modules() -> list[str]:
    missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
    if not missing:
        return []
    return ["Missing GAIA dependencies: " + ", ".join(missing) + ". Run pip install -e ."]


def check_model_config() -> list[str]:
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if provider == "dev":
        missing = [name for name in ("DEV_API_BASE", "DEV_API_KEY") if not os.environ.get(name)]
        return ["Missing OpenAI-compatible configuration: " + ", ".join(missing)] if missing else []
    if provider in ("", "anthropic") and os.environ.get("ANTHROPIC_API_KEY"):
        missing = [name for name in ("ANTHROPIC_MODEL",) if not os.environ.get(name)]
        return ["Missing native Anthropic configuration: " + ", ".join(missing)] if missing else []
    if provider == "venus":
        # Match core.llm: an OpenAI-compatible gateway may reuse the
        # ANTHROPIC_* variables from an earlier native-provider attempt.
        missing = []
        if not (os.environ.get("VENUS_API_BASE") or os.environ.get("ANTHROPIC_API_BASE")):
            missing.append("VENUS_API_BASE or ANTHROPIC_API_BASE")
        if not (os.environ.get("VENUS_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
            missing.append("VENUS_API_KEY or ANTHROPIC_API_KEY")
        return ["Missing OpenAI-compatible gateway configuration: " + ", ".join(missing)] if missing else []
    return ["No valid LLM configuration. Set LLM_PROVIDER with its required API variables."]


def attachment_exists(task_id: str, file_name: str) -> bool:
    return (FILES_DIR / file_name).exists() or (FILES_DIR / task_id / file_name).exists()


def check_dataset(split: str) -> list[str]:
    missing_data = []
    missing_attachments: list[str] = []
    for level in (1, 2, 3):
        path = DATA_DIR / f"level_{level}_{split}.json"
        if not path.exists():
            missing_data.append(str(path.relative_to(ROOT)))
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            file_name = item.get("file_name", "")
            if file_name and not attachment_exists(item["task_id"], file_name):
                missing_attachments.append(f"{item['task_id']}/{file_name}")
    errors = []
    if missing_data:
        errors.append("Missing GAIA split files: " + ", ".join(missing_data))
    if missing_attachments:
        preview = ", ".join(missing_attachments[:5])
        suffix = " ..." if len(missing_attachments) > 5 else ""
        errors.append(
            f"Missing {len(missing_attachments)} GAIA attachments under data/gaia/val_files: "
            f"{preview}{suffix}. Download the GAIA attachment files before running."
        )
    return errors


async def check_network() -> list[str]:
    sys.path.insert(0, str(ROOT))
    from tools.web_fetch import execute as fetch
    from tools.web_search import execute as search

    errors = []
    search_result = await search('site:en.wikipedia.org "Giganotosaurus"')
    if search_result.startswith("["):
        errors.append("web_search is unavailable or returned no usable result: " + search_result[:160])
    # Wikimedia REST occasionally replies 429 to a shared proxy IP.  A short,
    # bounded retry distinguishes that transient condition from a genuinely
    # unavailable fetch path before an expensive experiment is started.
    fetch_result = ""
    for attempt in range(3):
        fetch_result = await fetch("https://en.wikipedia.org/wiki/Giganotosaurus")
        if fetch_result.startswith("[Wikipedia:"):
            break
        if "HTTP 429" not in fetch_result or attempt == 2:
            break
        await asyncio.sleep(2 * (attempt + 1))
    if not fetch_result.startswith("[Wikipedia:"):
        errors.append("web_fetch cannot reach Wikipedia: " + fetch_result[:160])
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("train_20", "test_100", "all"), default="all")
    args = parser.parse_args()

    load_project_env()
    configure_macos_proxy()
    errors = [*check_python(), *check_modules(), *check_model_config()]
    splits = ("train_20", "test_100") if args.split == "all" else (args.split,)
    for split in splits:
        errors.extend(check_dataset(split))
    if not errors:
        errors.extend(asyncio.run(check_network()))

    if errors:
        print("GAIA preflight: FAILED")
        for error in errors:
            print("- " + error)
        return 1

    print("GAIA preflight: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
