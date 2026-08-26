#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/logs/beyond_locobench_hourly_monitor.log"
mkdir -p "$ROOT/logs"

while :; do
  {
    printf '[%s] hourly status\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    ps ax -o pid,etime,stat,command | grep -E 'adapter_(beyondswe|locobench)' | grep -v grep || echo 'no benchmark process found'
    for f in "$ROOT/logs/beyond_train_test_restart4.log" "$ROOT/logs/locobench_train_test_restart4.log"; do
      if [[ -f "$f" ]]; then
        printf '%s: %s bytes, last event: ' "$(basename "$f")" "$(wc -c < "$f" | tr -d ' ')"
        tail -n 1 "$f"
      fi
    done
  } >> "$LOG" 2>&1
  sleep 3600
done
