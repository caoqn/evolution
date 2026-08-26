#!/usr/bin/env bash
# Run the two author-script LoCoBench evolution phases sequentially and freeze
# their final six-agent snapshots.  This wrapper deliberately delegates each
# evolution phase to run_locobench_paper_experiment.sh unchanged.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$ROOT/.venv311/bin:$PATH"

freeze_latest() {
    local category="$1"
    local target_pool="$2"
    local run_dir

    run_dir="$(find "$ROOT/runs" -maxdepth 1 -mindepth 1 -type d \
        -name "*_locobench_evolve_${category}_evolve" -print | sort | tail -n 1)"
    test -n "$run_dir"
    test -d "$run_dir/team"
    bash "$ROOT/scripts/freeze_evolved_team.sh" \
        "${run_dir##*/}" latest "$target_pool"
}

echo "[$(date '+%F %T')] Begin six-agent LoCoBench full training"

bash "$ROOT/scripts/run_locobench_paper_experiment.sh" evolve fi
freeze_latest fi pool_LoCoBench_evolved_fi_final

bash "$ROOT/scripts/run_locobench_paper_experiment.sh" evolve cr
freeze_latest cr pool_LoCoBench_evolved_cr_final

echo "[$(date '+%F %T')] Completed six-agent LoCoBench full training"
