#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

STAMP="${1:-$(date +%Y%m%d_%H%M%S)}"
RUN_TAG="${2:-r1}"
PYTHON_BIN="${META_TEAM_PYTHON:-${ROOT}/.venv-loca/bin/python}"
ENV_FILE="${META_TEAM_ENV_FILE:-${ROOT}/apiconfig/beyond.env}"

mkdir -p runs/manifests runs/circuits

run_split() {
    local split="$1"
    local run_id="${STAMP}_newcode_beyondswe_${split}_train20_${RUN_TAG}"

    export META_TEAM_PYTHON="${PYTHON_BIN}"
    export META_TEAM_ENV_FILE="${ENV_FILE}"
    export META_TEAM_RESULT_MANIFEST="${ROOT}/runs/manifests/${run_id}.json"
    export META_TEAM_API_CIRCUIT_FILE="${ROOT}/runs/circuits/${run_id}.json"
    export BEYONDSWE_RUN_ID="${run_id}"

    echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] starting new-code ${split} training"
    echo "run_id=${run_id} source_pool=pool_BeyondSWE workers=1 effective=2400 wall=3600"
    bash scripts/run_beyondswe_paper_experiment.sh evolve "${split}"
}

# Keep the two independent evolution chains serial because they share one API endpoint.
run_split crossrepo
run_split depmigrate
