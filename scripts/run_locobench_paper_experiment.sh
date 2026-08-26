#!/usr/bin/env bash
# ============================================================================
# run_locobench_paper_experiment.sh
#
#   Feature Implementation:    100 total → 20 evolve + 80 holdout (Python)
#   Cross-File Refactoring:    100 total → 20 evolve + 80 holdout (Python)
#
# Usage:
#   bash scripts/run_locobench_paper_experiment.sh evolve fi
#   bash scripts/run_locobench_paper_experiment.sh evolve cr
#   bash scripts/run_locobench_paper_experiment.sh freeze <run_id> fi
#   bash scripts/run_locobench_paper_experiment.sh test fi pool_LoCoBench_evolved_fi --rollout 3
#   bash scripts/run_locobench_paper_experiment.sh test-ood fi pool_LoCoBench_evolved_fi c
# ============================================================================

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${META_TEAM_PYTHON:-python3}"

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

ACTION="${1:?Usage: $0 <evolve|freeze|test|test-ood|dry-run> ...}"
shift || true

category_name() {
    case "$1" in
        fi) echo "feature_implementation" ;;
        cr) echo "cross_file_refactoring" ;;
        *)  echo "$1" ;;
    esac
}

pool_suffix() {
    case "$1" in
        fi) echo "fi" ;;
        cr) echo "cr" ;;
        *)  echo "$1" ;;
    esac
}

EVOLVE_CASES="0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19"
HOLDOUT_CASES="$("${PYTHON_BIN}" -c "print(','.join(str(i) for i in range(20, 100)))")"

# ============================================================================
# Phase: evolve
# ============================================================================
phase_evolve() {
    local cat_short="${1:?Usage: $0 evolve <fi|cr>}"
    local category="$(category_name "$cat_short")"
    local run_id="${TS}_locobench_evolve_${cat_short}"

    log "LoCoBench evolve: category=$category, 20 cases (serial)"

    run_cmd "LoCoBench $category evolve (20 cases)" \
        "${PYTHON_BIN}" benchmarks/adapter_locobench.py \
            --split python \
            --category "$category" \
            --cases "$EVOLVE_CASES" \
            --team pool_LoCoBench \
            --evolve \
            --max-cost 30 \
            --timeout 1800 \
            --workers 1 \
            --run-id "$run_id"

    echo ""
    log "Next: bash scripts/freeze_evolved_team.sh ${run_id}_evolve latest pool_LoCoBench_evolved_${cat_short}_final"
}

# ============================================================================
# Phase: freeze
# ============================================================================
phase_freeze() {
    local run_id="${1:?Usage: $0 freeze <run_id> <fi|cr>}"
    local cat_short="${2:?Usage: $0 freeze <run_id> <fi|cr>}"
    local pool_name="pool_LoCoBench_evolved_${cat_short}_final"

    bash scripts/freeze_evolved_team.sh "$run_id" latest "$pool_name" --force
}

# ============================================================================
# ============================================================================
phase_test() {
    local cat_short="${1:?Usage: $0 test <fi|cr> <team> [--rollout N]}"
    local team="${2:?Usage: $0 test <fi|cr> <team>}"
    local category="$(category_name "$cat_short")"
    local rollouts=1

    shift 2
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rollout) rollouts="$2"; shift 2 ;;
            *) echo "Unknown: $1"; exit 1 ;;
        esac
    done

    log "LoCoBench holdout: category=$category, team=$team, N=80, rollouts=$rollouts"

    for r in $(seq 1 "$rollouts"); do
        local run_id="${TS}_locobench_test_${cat_short}_80_r${r}"
        run_cmd "LoCoBench $category holdout 80 (rollout $r/$rollouts)" \
            "${PYTHON_BIN}" benchmarks/adapter_locobench.py \
                --split python \
                --category "$category" \
                --cases "$HOLDOUT_CASES" \
                --team "$team" \
                --max-cost 30 \
                --timeout 1800 \
                --workers 4 \
                --run-id "$run_id"
    done
}

# ============================================================================
# ============================================================================
phase_test_ood() {
    local cat_short="${1:?Usage: $0 test-ood <fi|cr> <team> <c|cpp|java>}"
    local team="${2:?}"
    local lang="${3:?Usage: $0 test-ood <fi|cr> <team> <c|cpp|java>}"
    local category="$(category_name "$cat_short")"
    local run_id="${TS}_locobench_ood_${cat_short}_${lang}_100"

    run_cmd "LoCoBench OOD: $category × $lang (100 cases)" \
        "${PYTHON_BIN}" benchmarks/adapter_locobench.py \
            --split "$lang" \
            --category "$category" \
            --team "$team" \
            --max-cost 30 \
            --timeout 1800 \
            --workers 4 \
            --run-id "$run_id"
}

# ============================================================================
# Phase: dry-run
# ============================================================================
phase_dryrun() {
    DRY_RUN=1
    echo "=== LoCoBench Paper Experiment — Dry Run ==="
    echo ""
    phase_evolve fi
    echo ""
    phase_evolve cr
    echo ""
    echo "--- After evolve + freeze ---"
    echo ""
    phase_test fi pool_LoCoBench_evolved_fi_final --rollout 3
    echo ""
    phase_test cr pool_LoCoBench_evolved_cr_final --rollout 3
    echo ""
    echo "--- Out-of-domain ---"
    for lang in c cpp java; do
        phase_test_ood fi pool_LoCoBench_evolved_fi_final "$lang"
        phase_test_ood cr pool_LoCoBench_evolved_cr_final "$lang"
    done
}

case "$ACTION" in
    evolve)   phase_evolve "$@" ;;
    freeze)   phase_freeze "$@" ;;
    test)     phase_test "$@" ;;
    test-ood) phase_test_ood "$@" ;;
    dry-run)  phase_dryrun ;;
    *)
        cat <<EOF
Usage: $0 <phase> [options]

Phases:
  evolve <fi|cr>          Evolve on 20 Python instances (serial).
  freeze <run_id> <fi|cr> Freeze to agents/pool_LoCoBench_evolved_<fi|cr>_final/.
  test <fi|cr> <team> [--rollout N]  Holdout 80 Python instances.
  test-ood <fi|cr> <team> <c|cpp|java>  Out-of-domain 100 instances.
  dry-run                 Show all commands.

Paper Table 10:
  FI:  100 Python → 20 evolve + 80 holdout
  CR:  100 Python → 20 evolve + 80 holdout
  OOD: C/C++/Java 100 items each (test only, no evolve)
EOF
        ;;
esac
