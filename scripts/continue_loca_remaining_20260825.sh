#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${META_TEAM_PYTHON:-$ROOT/.venv-loca/bin/python}"
ENV_FILE="${META_TEAM_ENV_FILE:-$ROOT/apiconfig/loca.env}"
TEAM="pool_LOCAbench_evolved_final"
PREFIX="20260825_$(date +%H%M%S)_loca_eval_mt_v016_remaining"
TASKS=(WoocommerceNewWelcome WoocommerceStockAlert FilterLowSelling ApplyPhDEmail SetConfCrDdl CourseAssistant CanvasArrangeExam CanvasListTest)
run_task() {
  local split="$1" task="$2"
  echo "[$(date '+%F %T%z')] START ${split} ${task}"
  env META_TEAM_ENV_FILE="$ENV_FILE" LOCA_STRICT_PREPROCESS=1 \
    "$PYTHON_BIN" benchmarks/adapter_locabench.py --split "$split" --team "$TEAM" --task "$task" \
      --workers 3 --run-id "${PREFIX}_${split}_${task}"
  echo "[$(date '+%F %T%z')] DONE ${split} ${task}"
}
# 8K is complete; 16K WoocommerceNewWelcome is complete for all five seeds.
for task in WoocommerceStockAlert FilterLowSelling ApplyPhDEmail SetConfCrDdl CourseAssistant CanvasArrangeExam CanvasListTest; do
  run_task 16k "$task"
done
for split in 32k 64k 128k 256k; do
  for task in "${TASKS[@]}"; do
    run_task "$split" "$task"
  done
done
echo "[$(date '+%F %T%z')] ALL REMAINING LOCA CONFIGURATIONS COMPLETE"
