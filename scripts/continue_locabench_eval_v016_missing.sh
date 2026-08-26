#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
PYTHON_BIN="${META_TEAM_PYTHON:-${ROOT}/.venv-loca/bin/python}"
ENV_FILE="${META_TEAM_ENV_FILE:-${ROOT}/apiconfig/loca.env}"
TEAM="pool_LOCAbench_evolved_final"
PREFIX="20260824_$(date +%H%M%S)_loca_eval_mt_v016_missing"
TASKS=(WoocommerceNewWelcome WoocommerceStockAlert FilterLowSelling ApplyPhDEmail SetConfCrDdl CourseAssistant CanvasArrangeExam CanvasListTest)

run_task() {
  local split="$1" task="$2" seed="${3:-}"
  echo "[$(date '+%F %T%z')] START split=${split} task=${task}${seed:+ seed=${seed}}"
  args=(--split "${split}" --team "${TEAM}" --task "${task}" --workers 3 --run-id "${PREFIX}_${split}_${task}${seed:+_seed${seed}}")
  [[ -n "${seed}" ]] && args+=(--seed "${seed}")
  env META_TEAM_ENV_FILE="${ENV_FILE}" LOCA_STRICT_PREPROCESS=1 \
    "${PYTHON_BIN}" benchmarks/adapter_locabench.py "${args[@]}"
  echo "[$(date '+%F %T%z')] DONE split=${split} task=${task}${seed:+ seed=${seed}}"
}

# The original 43 results are retained. Only missing configurations are submitted.
run_task 8k SetConfCrDdl 789
run_task 16k WoocommerceNewWelcome 789
run_task 16k WoocommerceNewWelcome 2024
for split in 16k 32k 64k 128k 256k; do
  for task in "${TASKS[@]}"; do
    [[ "${split}" = 16k && "${task}" = WoocommerceNewWelcome ]] && continue
    run_task "${split}" "${task}"
  done
done
echo "[$(date '+%F %T%z')] ALL MISSING LOCA CONFIGURATIONS COMPLETE"
