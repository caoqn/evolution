#!/usr/bin/env bash
# Run GAIA and LOCA-Bench in separate processes with isolated API settings.
#
# Each process keeps its own API circuit-breaker state, result manifest, run
# directory, and evolved-team lineage.  Inside a dataset, --evolve remains
# serial; evaluation uses each adapter's --workers setting.
#
# Usage:
#   bash scripts/run_gaia_loca_parallel.sh evolve /abs/gaia.env /abs/loca.env
#   bash scripts/run_gaia_loca_parallel.sh test /abs/gaia.env <gaia_pool> \
#       /abs/loca.env <loca_pool> <loca_split>

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

MODE="${1:-}"
if [[ -z "${MODE}" ]]; then
    echo "Usage: $0 <evolve|test> ..." >&2
    exit 2
fi
shift

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p logs runs/circuits runs/manifests

require_env_file() {
    local file="$1"
    if [[ ! -f "${file}" ]]; then
        echo "API environment file not found: ${file}" >&2
        exit 2
    fi
}

start_job() {
    local label="$1"
    local env_file="$2"
    shift 2
    local circuit_file="${ROOT}/runs/circuits/${TIMESTAMP}_${label}.json"
    local manifest_file="${ROOT}/runs/manifests/${TIMESTAMP}_${label}.json"
    local log_file="${ROOT}/logs/${TIMESTAMP}_${label}.log"

    require_env_file "${env_file}"
    echo "[start] ${label}"
    echo "        log: ${log_file}"
    (
        export META_TEAM_ENV_FILE="${env_file}"
        export META_TEAM_API_CIRCUIT_FILE="${circuit_file}"
        export META_TEAM_RESULT_MANIFEST="${manifest_file}"
        export PYTHONUNBUFFERED=1
        exec "$@"
    ) >"${log_file}" 2>&1 &
    JOB_PIDS+=("$!")
    JOB_LABELS+=("${label}")
}

JOB_PIDS=()
JOB_LABELS=()

case "${MODE}" in
    evolve)
        if [[ $# -ne 2 ]]; then
            echo "Usage: $0 evolve /abs/gaia.env /abs/loca.env" >&2
            exit 2
        fi
        start_job "gaia_evolve" "$1" bash scripts/run_gaia_evolve_and_test.sh evolve
        start_job "loca_evolve" "$2" bash scripts/run_locabench_paper_experiment.sh evolve-mt --execute
        ;;
    test)
        if [[ $# -ne 5 ]]; then
            echo "Usage: $0 test /abs/gaia.env <gaia_pool> /abs/loca.env <loca_pool> <loca_split>" >&2
            exit 2
        fi
        gaia_env="$1"
        gaia_pool="$2"
        loca_env="$3"
        loca_pool="$4"
        loca_split="$5"
        start_job "gaia_test" "${gaia_env}" bash scripts/run_gaia_evolve_and_test.sh test "${gaia_pool}"
        start_job "loca_test" "${loca_env}" bash scripts/run_locabench_paper_experiment.sh eval-mt "${loca_split}" "${loca_pool}" --execute
        ;;
    *)
        echo "Unknown mode: ${MODE}. Use evolve or test." >&2
        exit 2
        ;;
esac

status=0
for i in "${!JOB_PIDS[@]}"; do
    if wait "${JOB_PIDS[$i]}"; then
        echo "[done] ${JOB_LABELS[$i]}"
    else
        code=$?
        echo "[failed] ${JOB_LABELS[$i]} (exit ${code}); see logs/${TIMESTAMP}_${JOB_LABELS[$i]}.log" >&2
        status=1
    fi
done
exit "${status}"
