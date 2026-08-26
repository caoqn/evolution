#!/bin/bash
# GAIA Evolution Pipeline — 20-train evolve + 100-test evaluation.
#
# Usage:
#   scripts/run_gaia_evolve_and_test.sh [evolve|test|smoke]
#   - smoke:  run 3 cases of train_20 (first 3 tasks) to validate the pipeline
#   - evolve: run full 20-case evolve (~4-6 hours)
#   - test:   run 100-case hold-out test on the evolved pool (~2 hours w/ -j4)
#
# Workflow:
#   1. smoke (3 cases)   → validates evolve pipeline end-to-end
#   2. evolve (20 cases) → produces runs/<ts>_evolve_gaia_train_20_evolve/
#   3. promote            → copy latest team/vNN to agents/pool_GAIA_MT_evolved_v0NN
#   4. test (100 cases)  → evaluates the promoted pool on hold-out

set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p logs

if [[ -z "${HTTPS_PROXY:-}" && -z "${https_proxy:-}" ]] && command -v scutil >/dev/null 2>&1; then
  PROXY_HOST="$(scutil --proxy 2>/dev/null | awk '/HTTPProxy :/{print $3; exit}')"
  PROXY_PORT="$(scutil --proxy 2>/dev/null | awk '/HTTPPort :/{print $3; exit}')"
  if [[ -n "${PROXY_HOST}" && "${PROXY_PORT}" =~ ^[0-9]+$ ]]; then
    PROXY_URL="http://${PROXY_HOST}:${PROXY_PORT}"
    export HTTP_PROXY="${PROXY_URL}" HTTPS_PROXY="${PROXY_URL}" ALL_PROXY="${PROXY_URL}"
    export http_proxy="${PROXY_URL}" https_proxy="${PROXY_URL}" all_proxy="${PROXY_URL}"
    echo "Using macOS system HTTP(S) proxy."
  fi
fi

PYTHON_BIN="${META_TEAM_PYTHON:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x ".venv311/bin/python" ]]; then
    PYTHON_BIN=".venv311/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

"${PYTHON_BIN}" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(
        f"Python 3.10+ is required, found {sys.version.split()[0]}. "
        "Set META_TEAM_PYTHON to the reproduction environment."
    )
PY

MODE="${1:-smoke}"
TS=$(date +%Y%m%d_%H%M%S)

case "$MODE" in
  smoke)
    # Default: 2 cases (web + file typical paths). Override with 2nd arg.
    CASES="${2:-0,1}"
    echo "=== SMOKE TEST (cases=${CASES} from train_20) ==="
    RUN_ID="${TS}_smoke_gaia_train_20"
    "${PYTHON_BIN}" scripts/preflight_gaia.py --split train_20
    "${PYTHON_BIN}" benchmarks/adapter_gaia.py \
        --split train_20 \
        --cases "${CASES}" \
        --evolve \
        --team pool_GAIA_MT \
        --run-id "${RUN_ID}" \
        2>&1 | tee "logs/${RUN_ID}.log"
    echo ""
    echo "Smoke run complete. Inspect:"
    echo "  runs/${RUN_ID}_evolve/summary.json"
    echo "  runs/${RUN_ID}_evolve/changelog.jsonl"
    echo "  runs/${RUN_ID}_evolve/team/v*/"
    ;;

  evolve)
    # A relay can occasionally need extra time to recover from a transient
    # upstream failure. Override with GAIA_EVOLVE_TIMEOUT when required.
    EVOLVE_TIMEOUT="${GAIA_EVOLVE_TIMEOUT:-3000}"
    EFFECTIVE_TIMEOUT="${GAIA_EFFECTIVE_TIMEOUT:-900}"
    echo "=== FULL EVOLVE (20 cases, ${EFFECTIVE_TIMEOUT}s effective budget, ${EVOLVE_TIMEOUT}s recovery cap) ==="
    RUN_ID="${GAIA_RUN_ID:-${TS}_evolve_gaia_train_20}"
    RESUME_ARGS=()
    if [[ "${GAIA_RESUME:-0}" == "1" ]]; then
      RESUME_ARGS+=(--resume)
      echo "=== RESUMING EXISTING EVOLVE RUN: ${RUN_ID} ==="
    fi
    "${PYTHON_BIN}" scripts/preflight_gaia.py --split train_20
    "${PYTHON_BIN}" benchmarks/adapter_gaia.py \
        --split train_20 \
        --evolve \
        --team pool_GAIA_MT \
        --timeout "${EVOLVE_TIMEOUT}" \
        --effective-timeout "${EFFECTIVE_TIMEOUT}" \
        --run-id "${RUN_ID}" \
        "${RESUME_ARGS[@]}" \
        2>&1 | tee "logs/${RUN_ID}.log"
    echo ""
    echo "Evolve run complete:"
    echo "  runs/${RUN_ID}_evolve/"
    echo ""
    echo "To promote the evolved team:"
    echo "  LATEST_V=\$(ls runs/${RUN_ID}_evolve/team/ | grep -E '^v[0-9]+$' | tail -1)"
    echo "  cp -r runs/${RUN_ID}_evolve/team/\$LATEST_V agents/pool_GAIA_MT_evolved_\$LATEST_V"
    ;;

  test)
    # Usage: scripts/run_gaia_evolve_and_test.sh test <evolved_pool_name> [--rollout N]
    POOL="${2:-}"
    if [[ -z "$POOL" ]]; then
        echo "Usage: $0 test <evolved_pool_name> [--rollout N]"
        echo "Example: $0 test pool_GAIA_MT_evolved --rollout 3"
        exit 1
    fi
    if [[ ! -d "agents/${POOL}" ]]; then
        echo "Pool not found: agents/${POOL}"
        exit 1
    fi
    shift 2
    ROLLOUTS=1
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rollout) ROLLOUTS="$2"; shift 2 ;;
            *) echo "Unknown arg: $1"; exit 1 ;;
        esac
    done
    echo "=== HOLD-OUT TEST (100 cases on ${POOL}, rollouts=${ROLLOUTS}) ==="
    TEST_TIMEOUT="${GAIA_TEST_TIMEOUT:-3000}"
    EFFECTIVE_TIMEOUT="${GAIA_EFFECTIVE_TIMEOUT:-900}"
    "${PYTHON_BIN}" scripts/preflight_gaia.py --split test_100
    for r in $(seq 1 "$ROLLOUTS"); do
        RUN_ID="${TS}_test_gaia_${POOL}_r${r}"
        echo "--- Rollout ${r}/${ROLLOUTS} (run_id: ${RUN_ID}) ---"
        "${PYTHON_BIN}" benchmarks/adapter_gaia.py \
            --split test_100 \
            --team "${POOL}" \
            --timeout "${TEST_TIMEOUT}" \
            --effective-timeout "${EFFECTIVE_TIMEOUT}" \
            --workers 6 \
            --run-id "${RUN_ID}" \
            2>&1 | tee "logs/${RUN_ID}.log"
    done
    echo ""
    echo "Test complete. Results in benchmarks/gaia-results/"
    ;;

  *)
    echo "Unknown mode: $MODE"
    echo "Usage: $0 [smoke|evolve|test]"
    exit 1
    ;;
esac
