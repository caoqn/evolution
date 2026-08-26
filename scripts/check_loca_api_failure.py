#!/usr/bin/env python3
"""Print the first completed LOCA result whose API failure caused task failure."""

from __future__ import annotations

import glob
import json
import sys


def main() -> int:
    root = sys.argv[1]
    for path in glob.glob(root + "/*/result.json"):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        reliability = data.get("api_reliability") or {}
        terminal = int(reliability.get("terminal_failure_count", 0) or 0)
        provider_evidence = bool(reliability.get("by_error_type"))
        error = str(data.get("run_error") or "").lower()
        api_error = any(
            token in error
            for token in (
                "badgateway",
                "rate_limit",
                "ratelimit",
                "status=429",
                "status=502",
                "status=524",
                "provider",
            )
        )
        failed = data.get("success") is False or float(data.get("score", 0) or 0) <= 0
        if (api_error or provider_evidence or terminal > 0) and failed:
            print(path)
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
