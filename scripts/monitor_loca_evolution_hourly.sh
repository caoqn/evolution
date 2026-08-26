#!/usr/bin/env bash

# Hourly guard for one LOCA evolution run.  It only reacts to terminal
# provider/infrastructure evidence recorded in a completed result.json.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ID="${1:?usage: $0 <run_id>}"
RUN_DIR="$ROOT/runs/$RUN_ID"
LOG="$ROOT/logs/${RUN_ID}_hourly_monitor.log"
ROLLBACK_POOL="pool_LOCAbench_rollback_${RUN_ID}"

mkdir -p "$ROOT/logs"

latest_version() {
  find "$RUN_DIR/team" -mindepth 1 -maxdepth 1 -type d -name 'v[0-9]*' -exec basename {} \; 2>/dev/null \
    | sort -V | tail -1
}

while :; do
  now="$(date '+%Y-%m-%d %H:%M:%S %z')"
  trigger=""
  if [[ -d "$RUN_DIR/cases" ]]; then
    trigger="$(${ROOT}/.venv-loca/bin/python "$ROOT/scripts/check_loca_api_failure.py" "$RUN_DIR/cases")"
  fi

  {
    printf '[%s] run=%s results=%s latest_version=%s\n' "$now" "$RUN_ID" \
      "$(find "$RUN_DIR/cases" -name result.json 2>/dev/null | wc -l | tr -d ' ')" \
      "$(latest_version)"
  } >> "$LOG" 2>&1

  if [[ -n "$trigger" ]]; then
    version="$(latest_version)"
    {
      printf '[%s] TERMINAL_API_FAILURE result=%s\n' "$now" "$trigger"
      printf '[%s] stopping run and preserving rollback version=%s\n' "$now" "${version:-none}"
    } >> "$LOG"

    # Match only the target run, excluding this monitor itself.
    while read -r pid; do
      [[ -n "$pid" ]] && kill -TERM "$pid" 2>/dev/null || true
    done < <(ps -axo pid=,command= | awk -v run="$RUN_ID" '$0 ~ run && $0 !~ /monitor_loca_evolution_hourly/ && $0 !~ /awk/ {print $1}')

    if [[ -n "$version" && ! -d "$ROOT/agents/$ROLLBACK_POOL" ]]; then
      bash "$ROOT/scripts/freeze_evolved_team.sh" "$RUN_ID" "$version" "$ROLLBACK_POOL" >> "$LOG" 2>&1 || true
    fi
    printf '[%s] guard finished; no further checks\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" >> "$LOG"
    exit 0
  fi

  sleep 3600
done
