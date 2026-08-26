#!/usr/bin/env bash
# Continue the interrupted v016 LOCA evaluation without rerunning completed cases.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

PYTHON_BIN="${META_TEAM_PYTHON:-${ROOT}/.venv-loca/bin/python}"
ENV_FILE="${META_TEAM_ENV_FILE:-${ROOT}/apiconfig/loca.env}"
TEAM="pool_LOCAbench_evolved_final"
PREFIX="20260824_$(date +%H%M%S)_loca_eval_mt_v016_continue"

run_task() {
    local split="$1"
    local task="$2"
    shift 2
    env META_TEAM_ENV_FILE="${ENV_FILE}" LOCA_STRICT_PREPROCESS=1 \
        "${PYTHON_BIN}" benchmarks/adapter_locabench.py \
        --split "${split}" --team "${TEAM}" --task "${task}" \
        --workers 3 --run-id "${PREFIX}_${split}_${task}" "$@"
}

# The original 8K SetConfCrDdl sub-run lost only seed 2024 before result.json.
run_task 8k SetConfCrDdl --seed 2024

# The remaining 8K tasks have not started.
for task in CourseAssistant CanvasArrangeExam CanvasListTest; do
    run_task 8k "${task}"
done

# All six task families below are entirely unstarted.
for split in 16k 32k 64k 128k 256k; do
    for task in WoocommerceNewWelcome WoocommerceStockAlert FilterLowSelling ApplyPhDEmail SetConfCrDdl CourseAssistant CanvasArrangeExam CanvasListTest; do
        run_task "${split}" "${task}"
    done
done
