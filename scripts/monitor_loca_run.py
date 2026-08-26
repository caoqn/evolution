#!/usr/bin/env python3
"""Hourly safety monitor for one LoCA run."""

from __future__ import annotations

import argparse
import os
import re
import signal
import time
from pathlib import Path


ERROR_RE = re.compile(
    r"no tools discovered|MCP initialization.*incomplete|Client failed to connect|"
    r"APIConnectionError|AuthenticationError|Unauthorized|All available accounts exhausted",
    re.IGNORECASE,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--interval", type=int, default=3600)
    args = parser.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            os.kill(args.pid, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            pass
        text = "\n".join(
            p.read_text(errors="replace")
            for p in args.run.rglob("*")
            if p.is_file() and p.stat().st_size < 20_000_000
        )
        stamp = time.strftime("%Y-%m-%d %H:%M:%S %z")
        match = ERROR_RE.search(text)
        if match:
            with args.log.open("a") as stream:
                stream.write(f"[{stamp}] fatal pattern: {match.group(0)}\n")
            try:
                os.kill(args.pid, signal.SIGINT)
                time.sleep(10)
                os.kill(args.pid, 0)
                os.kill(args.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            return
        result_count = sum(1 for _ in (args.run / "cases").glob("*/result.json"))
        with args.log.open("a") as stream:
            stream.write(f"[{stamp}] alive pid={args.pid} results={result_count}\n")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
