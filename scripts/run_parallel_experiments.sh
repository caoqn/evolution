#!/usr/bin/env bash
# Launch 2--3 independent Meta-Team experiments concurrently.
#
# This is intentionally benchmark-agnostic: the command after each --job can
# invoke any existing benchmark pipeline or adapter.  A job is a *process*,
# so its LLM provider/base URL is isolated from every other job.
#
# Example:
#   bash scripts/run_parallel_experiments.sh --max-parallel 3 \
#     --job gaia /private/gaia.env -- \
#       bash scripts/run_gaia_evolve_and_test.sh evolve \
#     --job locobench-fi /private/loco.env -- \
#       bash scripts/run_locobench_paper_experiment.sh evolve fi \
#     --job swe-ansible /private/swe.env -- \
#       bash scripts/run_swebench_pro_paper_experiment.sh evolve ansible
#
# Do not put a literal top-level --job in a job command. If an adapter needs
# that uncommon argument, wrap it in a small private shell script first.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

MAX_PARALLEL=3
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p logs runs/circuits runs/manifests

PIDS=()
LABELS=()

usage() {
    cat <<'EOF'
Usage:
  bash scripts/run_parallel_experiments.sh --max-parallel <2-or-3> \
    --job <label> </absolute/path/api.env> -- <command...> \
    --job <label> </absolute/path/api.env> -- <command...> [...]

Each job receives an isolated META_TEAM_ENV_FILE, API circuit file, result
manifest, and log. Use independent run IDs/team-version directories for
training jobs. The command after -- may be any Meta-Team benchmark pipeline.
EOF
}

wait_for_oldest() {
    local pid="${PIDS[0]}"
    local label="${LABELS[0]}"
    local exit_code=0
    if wait "${pid}"; then
        echo "[done] ${label}"
    else
        exit_code=$?
        echo "[failed] ${label} (exit ${exit_code}); see logs/${TIMESTAMP}_${label}.log" >&2
        FAILED=1
    fi
    PIDS=("${PIDS[@]:1}")
    LABELS=("${LABELS[@]:1}")
}

start_job() {
    local label="$1"
    local env_file="$2"
    shift 2
    local circuit_file="${ROOT}/runs/circuits/${TIMESTAMP}_${label}.json"
    local manifest_file="${ROOT}/runs/manifests/${TIMESTAMP}_${label}.json"
    local log_file="${ROOT}/logs/${TIMESTAMP}_${label}.log"

    if [[ ! "${label}" =~ ^[A-Za-z0-9._-]+$ ]]; then
        echo "Invalid job label '${label}'; use letters, digits, ., _, or -." >&2
        exit 2
    fi
    if [[ ! -f "${env_file}" ]]; then
        echo "API environment file not found for ${label}: ${env_file}" >&2
        exit 2
    fi
    if [[ $# -eq 0 ]]; then
        echo "Job '${label}' has no command." >&2
        exit 2
    fi
    while [[ ${#PIDS[@]} -ge ${MAX_PARALLEL} ]]; do
        wait_for_oldest
    done

    echo "[start] ${label}"
    echo "        command: $*"
    echo "        log: ${log_file}"
    (
        export META_TEAM_ENV_FILE="${env_file}"
        export META_TEAM_API_CIRCUIT_FILE="${circuit_file}"
        export META_TEAM_RESULT_MANIFEST="${manifest_file}"
        export PYTHONUNBUFFERED=1
        exec "$@"
    ) >"${log_file}" 2>&1 &
    PIDS+=("$!")
    LABELS+=("${label}")
}

FAILED=0
JOB_COUNT=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-parallel)
            [[ $# -ge 2 ]] || { echo "--max-parallel needs a value" >&2; exit 2; }
            MAX_PARALLEL="$2"
            if [[ ! "${MAX_PARALLEL}" =~ ^[1-9][0-9]*$ ]]; then
                echo "--max-parallel must be a positive integer" >&2
                exit 2
            fi
            shift 2
            ;;
        --job)
            [[ $# -ge 4 ]] || { echo "--job needs: <label> <api.env> -- <command>" >&2; exit 2; }
            label="$2"
            env_file="$3"
            shift 3
            if [[ "${1:-}" != "--" ]]; then
                echo "Expected -- before the command for job '${label}'" >&2
                exit 2
            fi
            shift
            command=()
            while [[ $# -gt 0 && "$1" != "--job" ]]; do
                command+=("$1")
                shift
            done
            start_job "${label}" "${env_file}" "${command[@]}"
            JOB_COUNT=$((JOB_COUNT + 1))
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown launcher option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ${JOB_COUNT} -lt 2 ]]; then
    echo "Provide at least two independent --job entries." >&2
    exit 2
fi

while [[ ${#PIDS[@]} -gt 0 ]]; do
    wait_for_oldest
done
exit "${FAILED}"
