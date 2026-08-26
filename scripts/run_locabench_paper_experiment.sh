#!/usr/bin/env bash
#
# run_locabench_paper_experiment.sh
#
# - Evaluation set: 8 tasks × {8K,16K,32K,64K,128K,256K} × seeds {42,123,456,789,2024}
#
#
# Usage:
#   bash scripts/run_locabench_paper_experiment.sh evolve-mt [--execute]
#   bash scripts/run_locabench_paper_experiment.sh freeze <run_id> <version> [--force]
#   bash scripts/run_locabench_paper_experiment.sh eval-sa <split> [--execute]
#   bash scripts/run_locabench_paper_experiment.sh eval-mt <split> <team> [--execute]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

PYTHON_BIN="${META_TEAM_PYTHON:-${ROOT}/.venv-loca/bin/python}"
ENV_FILE="${META_TEAM_ENV_FILE:-${ROOT}/apiconfig/loca.env}"
export PATH="$(dirname "${PYTHON_BIN}"):${PATH}"

TARGET_TASKS=(
    WoocommerceNewWelcome
    WoocommerceStockAlert
    FilterLowSelling
    ApplyPhDEmail
    SetConfCrDdl
    CourseAssistant
    CanvasArrangeExam
    CanvasListTest
)

STD_SEEDS=(42 123 456 789 2024)

EVAL_SPLITS=(8k 16k 32k 64k 128k 256k)

ACTION="${1:-}"
shift || true

TIMESTAMP=$(date +%Y%m%d_%H%M%S)


run_cmd() {
    local execute_flag="$1"; shift
    echo "[cmd] $*"
    if [[ "${execute_flag}" == "--execute" ]]; then
        "$@"
    else
        echo "[dry-run] add --execute to run"
    fi
}

# ----- Phase 1: Evolution set (MT) -----
phase_evolve_mt() {
    local execute_flag="${1:-}"
    local run_id="${LOCA_RUN_ID:-${TIMESTAMP}_loca_evolve_mt_96k}"
    if [[ "${LOCA_RESUME:-0}" == "1" ]]; then
        echo "=== RESUMING EXISTING EVOLVE RUN: ${run_id} ==="
    fi

    echo "=== Phase 1: Evolution set (MT, 16 cases @ 96K, seeds {101,102}) ==="
    echo "Run ID: ${run_id}"
    echo "Team:   pool_LOCAbench (cold-start template registry)"
    echo ""

    cmd=(
        env META_TEAM_ENV_FILE="${ENV_FILE}" LOCA_STRICT_PREPROCESS=1
        "${PYTHON_BIN}" benchmarks/adapter_locabench.py
        --split evolve_96k
        --team pool_LOCAbench
        --evolve
        --max-cost 180
        --timeout "${LOCA_TRAIN_TIMEOUT:-7200}"
        --effective-timeout "${LOCA_TRAIN_EFFECTIVE_TIMEOUT:-7200}"
        --workers 1
        --run-id "${run_id}"
    )
    if [[ "${LOCA_RESUME:-0}" == "1" ]]; then
        cmd+=(--resume)
    fi
    run_cmd "${execute_flag}" "${cmd[@]}"
}

phase_freeze() {
    local run_id="${1:-}"
    local version="${2:-latest}"
    local force_flag="${3:-}"
    if [[ -z "${run_id}" ]]; then
        echo "Usage: $0 freeze <run_id> [version] [--force]" >&2
        exit 1
    fi
    local dest="pool_LOCAbench_evolved_final"
    bash scripts/freeze_evolved_team.sh "${run_id}" "${version}" "${dest}" ${force_flag}
}

# ----- Phase 3: Evaluation SA (baseline) -----
phase_eval_sa() {
    local split="${1:-}"
    local execute_flag="${2:-}"
    if [[ -z "${split}" ]]; then
        echo "Usage: $0 eval-sa <split> [--execute]" >&2
        exit 1
    fi
    local run_id="${TIMESTAMP}_loca_eval_sa_${split}"

    local task_csv
    task_csv=$(IFS=,; echo "${TARGET_TASKS[*]}")

    echo "=== Phase 3 (SA): Evaluation on ${split} (SA baseline) ==="
    echo "Run ID: ${run_id}"
    echo "Team:   pool_LOCAbench_single"
    echo "Tasks:  ${task_csv}"
    echo "Seeds:  ${STD_SEEDS[*]}"
    echo ""

    for task in "${TARGET_TASKS[@]}"; do
        local sub_run_id="${run_id}_${task}"
        cmd=(
            env META_TEAM_ENV_FILE="${ENV_FILE}" LOCA_STRICT_PREPROCESS=1
            "${PYTHON_BIN}" benchmarks/adapter_locabench.py
            --split "${split}"
            --team pool_LOCAbench_single
            --task "${task}"
            --workers "${LOCA_TEST_WORKERS:-3}"
            --run-id "${sub_run_id}"
        )
        run_cmd "${execute_flag}" "${cmd[@]}"
    done
}

# ----- Phase 4: Evaluation MT (evolved team) -----
phase_eval_mt() {
    local split="${1:-}"
    local team="${2:-pool_LOCAbench_evolved_final}"
    local execute_flag="${3:-}"
    if [[ -z "${split}" ]]; then
        echo "Usage: $0 eval-mt <split> <team> [--execute]" >&2
        exit 1
    fi
    local run_id="${TIMESTAMP}_loca_eval_mt_${split}"

    echo "=== Phase 4 (MT): Evaluation on ${split} using evolved team ==="
    echo "Run ID: ${run_id}"
    echo "Team:   ${team}"
    echo "Tasks:  ${TARGET_TASKS[*]}"
    echo "Seeds:  ${STD_SEEDS[*]}"
    echo ""

    for task in "${TARGET_TASKS[@]}"; do
        local sub_run_id="${run_id}_${task}"
        cmd=(
            env META_TEAM_ENV_FILE="${ENV_FILE}" LOCA_STRICT_PREPROCESS=1
            "${PYTHON_BIN}" benchmarks/adapter_locabench.py
            --split "${split}"
            --team "${team}"
            --task "${task}"
            --workers "${LOCA_TEST_WORKERS:-3}"
            --run-id "${sub_run_id}"
        )
        run_cmd "${execute_flag}" "${cmd[@]}"
    done
}

phase_eval_all() {
    local team="${1:-pool_LOCAbench_evolved_final}"
    local execute_flag="${2:-}"
    for split in "${EVAL_SPLITS[@]}"; do
        phase_eval_mt "${split}" "${team}" "${execute_flag}"
        echo ""
    done
}


case "${ACTION}" in
    evolve-mt)
        phase_evolve_mt "${1:-}"
        ;;
    freeze)
        phase_freeze "$@"
        ;;
    eval-sa)
        phase_eval_sa "$@"
        ;;
    eval-mt)
        phase_eval_mt "$@"
        ;;
    eval-all)
        phase_eval_all "$@"
        ;;
    smoke-16k)
        echo "=== Smoke test: 16K SA × 8 tasks × seed=42 ==="
        for task in "${TARGET_TASKS[@]}"; do
            local sub_run_id="${TIMESTAMP}_loca_smoke_16k_sa_${task}"
            cmd=(
                env META_TEAM_ENV_FILE="${ENV_FILE}" LOCA_STRICT_PREPROCESS=1
                "${PYTHON_BIN}" benchmarks/adapter_locabench.py
                --split 16k
                --team pool_LOCAbench_single
                --task "${task}"
                --seed 42
                --run-id "${sub_run_id}"
            )
            run_cmd "${1:-}" "${cmd[@]}"
        done
        ;;
    preflight)
        echo "# Preflight: show all phases without executing"
        echo ""
        phase_evolve_mt
        echo ""
        for split in "${EVAL_SPLITS[@]}"; do
            phase_eval_sa "${split}"
            phase_eval_mt "${split}" pool_LOCAbench_evolved_final
        done
        ;;
    *)
        cat <<EOF
Usage: $0 <phase> [...args]

Phases:
  evolve-mt [--execute]
      Run the Evolution set (16 cases, 96K, seeds {101,102})
      from pool_LOCAbench with --evolve.

  freeze <run_id> [version] [--force]
      Freeze runs/<run_id>/team/<version>/ to agents/pool_LOCAbench_evolved_final/.
      version defaults to 'latest'.

  eval-sa <split> [--execute]
      Run Evaluation set (40 cases = 8 tasks × 5 seeds) at given split
      using pool_LOCAbench_single baseline.

  eval-mt <split> <team> [--execute]
      Run Evaluation set at given split using a specified team
      (typically pool_LOCAbench_evolved_final after freezing).

  eval-all <team> [--execute]
      Run Evaluation on all 6 splits (8K, 16K, 32K, 64K, 128K, 256K).

  smoke-16k [--execute]
      Smoke test: 16K SA × 8 tasks × seed=42 (validate timeout/cost).

  preflight
      Dry-run all phases to inspect commands.

Environment variables set by this script:
  LOCA_STRICT_PREPROCESS=1    fail-fast on env preprocess failures

Eval splits:           ${EVAL_SPLITS[*]}
Evolution seeds:       101, 102
Evaluation seeds:      ${STD_SEEDS[*]}
Target tasks (8):
$(printf '  - %s\n' "${TARGET_TASKS[@]}")

Dually-isolated guarantee:
  - Evolution (seed ∈ {101,102}, context=96K)
  - Evaluation (seed ∈ {42,123,456,789,2024}, context ∈ {8K,16K,32K,64K,128K,256K})
  - Both (seed, context) sets are disjoint → cross-seed AND cross-scale generalization.
EOF
        ;;
esac
