#!/usr/bin/env bash
# ============================================================================
# run_rr_paper_experiment.sh
#
#
#   Backbone: Claude Sonnet 4.6
#   Judge: Claude Sonnet 4.6 (temperature=0)
#
#
# Phases:
#
#
# Usage:
#   bash scripts/run_rr_paper_experiment.sh evolve
#   bash scripts/run_rr_paper_experiment.sh freeze <evolve_run_id>
#   bash scripts/run_rr_paper_experiment.sh test pool_DeepResearch_evolved_rr --rollout 3
#   bash scripts/run_rr_paper_experiment.sh baseline --rollout 3
# ============================================================================

set -euo pipefail
cd "$(dirname "$0")/.."

TS="$(date +%Y%m%d_%H%M%S)"
export META_TEAM_MODEL="${META_TEAM_MODEL:-claude-sonnet-4.6}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

run_cmd() {
    local desc="$1"; shift
    echo "==========================================================="
    log "$desc"
    echo "==========================================================="
    echo "+ $*"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "[dry-run] skipped"
        return 0
    fi
    "$@"
}

ACTION="${1:?Usage: $0 <evolve|freeze|test|baseline|gen-splits|dry-run> ...}"
shift || true

SPLITS_DIR="data/splits"
EVOLVE_SPLIT="${SPLITS_DIR}/rr_paper_evolve_20.json"
HOLDOUT_SPLIT="${SPLITS_DIR}/rr_paper_holdout_81.json"

# ============================================================================
# ============================================================================
phase_gen_splits() {
    log "Generating paper splits (20 evolve + 81 holdout) ..."
    python3 << 'PYEOF'
import json, random
from pathlib import Path

BASE = Path("data/researchrubrics/researchrubrics.jsonl")
items = [json.loads(l) for l in BASE.read_text().splitlines() if l.strip()]
print(f"  Total RR items: {len(items)}")

random.seed(42)
ids = [it["sample_id"] for it in items]
random.shuffle(ids)

evolve_ids = ids[:20]
holdout_ids = ids[20:]

assert len(evolve_ids) == 20
assert len(holdout_ids) == 81
assert set(evolve_ids) & set(holdout_ids) == set()

splits_dir = Path("data/splits")
splits_dir.mkdir(parents=True, exist_ok=True)

evolve_split = {
    "name": "rr_paper_evolve_20",
    "description": "20 random instances for Meta-Team evolution (seed=42)",
    "sample_ids": evolve_ids,
}
holdout_split = {
    "name": "rr_paper_holdout_81",
    "description": "81 remaining instances for holdout evaluation (seed=42)",
    "sample_ids": holdout_ids,
}

(splits_dir / "rr_paper_evolve_20.json").write_text(
    json.dumps(evolve_split, indent=2) + "\n")
(splits_dir / "rr_paper_holdout_81.json").write_text(
    json.dumps(holdout_split, indent=2) + "\n")

print(f"  Created: {splits_dir}/rr_paper_evolve_20.json ({len(evolve_ids)} ids)")
print(f"  Created: {splits_dir}/rr_paper_holdout_81.json ({len(holdout_ids)} ids)")
PYEOF
}

# ============================================================================
# ============================================================================
phase_evolve() {
    if [[ ! -f "$EVOLVE_SPLIT" ]]; then
        log "Split file not found: $EVOLVE_SPLIT"
        log "Run: bash scripts/run_rr_paper_experiment.sh gen-splits"
        exit 1
    fi

    local run_id="${TS}_rr_paper_evolve_20"

    run_cmd "ResearchRubrics evolve (20 cases, serial)" \
        python3 benchmarks/adapter_researchrubrics.py \
            --split-file "$EVOLVE_SPLIT" \
            --team pool_DeepResearch \
            --evolve \
            --timeout 1800 \
            --max-cost 50.0 \
            --run-id "$run_id"

    echo ""
    log "Next: bash scripts/run_rr_paper_experiment.sh freeze ${run_id}_evolve"
}

# ============================================================================
# Phase: freeze
# ============================================================================
phase_freeze() {
    local run_id="${1:?Usage: $0 freeze <run_id>}"
    local pool_name="pool_DeepResearch_evolved_rr_paper"

    bash scripts/freeze_evolved_team.sh "$run_id" latest "$pool_name" --force
}

# ============================================================================
# ============================================================================
phase_test() {
    local team="${1:?Usage: $0 test <team> [--rollout N]}"
    local rollouts=1

    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rollout) rollouts="$2"; shift 2 ;;
            *) echo "Unknown: $1"; exit 1 ;;
        esac
    done

    if [[ ! -f "$HOLDOUT_SPLIT" ]]; then
        log "Split file not found: $HOLDOUT_SPLIT"
        log "Run: bash scripts/run_rr_paper_experiment.sh gen-splits"
        exit 1
    fi

    log "ResearchRubrics holdout: team=$team, N=81, rollouts=$rollouts"

    for r in $(seq 1 "$rollouts"); do
        local run_id="${TS}_rr_paper_holdout81_r${r}"
        run_cmd "ResearchRubrics holdout (81 cases, rollout $r/$rollouts)" \
            python3 benchmarks/adapter_researchrubrics.py \
                --split-file "$HOLDOUT_SPLIT" \
                --team "$team" \
                --timeout 1800 \
                --max-cost 50.0 \
                --workers 4 \
                --run-id "$run_id"
    done
}

# ============================================================================
# ============================================================================
phase_baseline() {
    local rollouts=1
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rollout) rollouts="$2"; shift 2 ;;
            *) echo "Unknown: $1"; exit 1 ;;
        esac
    done

    for r in $(seq 1 "$rollouts"); do
        # SA baseline
        run_cmd "RR SA baseline (101 cases, rollout $r/$rollouts)" \
            python3 benchmarks/adapter_researchrubrics.py \
                --team pool_DeepResearch_SA \
                --timeout 1800 \
                --max-cost 50.0 \
                --workers 4 \
                --run-id "${TS}_rr_sa_101_r${r}"

        # MAS baseline (v000, no evolve)
        run_cmd "RR MAS baseline (101 cases, rollout $r/$rollouts)" \
            python3 benchmarks/adapter_researchrubrics.py \
                --team pool_DeepResearch \
                --timeout 1800 \
                --max-cost 50.0 \
                --workers 4 \
                --run-id "${TS}_rr_mas_101_r${r}"
    done
}

# ============================================================================
# Phase: dry-run
# ============================================================================
phase_dryrun() {
    DRY_RUN=1
    echo "=== ResearchRubrics Paper Experiment — Dry Run ==="
    echo ""
    phase_gen_splits 2>/dev/null || true
    echo ""
    phase_evolve
    echo ""
    echo "--- After evolve + freeze ---"
    echo ""
    phase_test pool_DeepResearch_evolved_rr_paper --rollout 3
    echo ""
    phase_baseline --rollout 3
}

case "$ACTION" in
    gen-splits) phase_gen_splits ;;
    evolve)     phase_evolve "$@" ;;
    freeze)     phase_freeze "$@" ;;
    test)       phase_test "$@" ;;
    baseline)   phase_baseline "$@" ;;
    dry-run)    phase_dryrun ;;
    *)
        cat <<EOF
Usage: $0 <phase> [options]

Phases:
  gen-splits             Generate 20/81 split JSONs (seed=42, deterministic)
  evolve                 Evolve on 20 instances (serial)
  freeze <run_id>        Freeze to agents/pool_DeepResearch_evolved_rr_paper/
  test <team> [--rollout N]  Holdout 81 instances (avg@N)
  baseline [--rollout N]     SA + MAS on all 101 (for other Table 1 rows)
  dry-run                Show all commands

Paper setup:
  101 total → 20 evolve + 81 holdout
  Backbone: Claude Sonnet 4.6
  Judge: Claude Sonnet 4.6 (temperature=0)
  All results reported as avg@3
EOF
        ;;
esac
