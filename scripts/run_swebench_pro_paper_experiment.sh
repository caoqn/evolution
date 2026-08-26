#!/usr/bin/env bash
# ============================================================================
# run_swebench_pro_paper_experiment.sh
#
#   Ansible:     96 total = 20 evolve + 76 holdout
#   Qutebrowser: 79 total = 20 evolve + 59 holdout
#
# Usage:
#   bash scripts/run_swebench_pro_paper_experiment.sh evolve ansible
#   bash scripts/run_swebench_pro_paper_experiment.sh evolve qutebrowser
#   bash scripts/run_swebench_pro_paper_experiment.sh freeze <run_id> ansible
#   bash scripts/run_swebench_pro_paper_experiment.sh test ansible pool_SWE_Pro_evolved_ansible --rollout 3
#   bash scripts/run_swebench_pro_paper_experiment.sh test qutebrowser pool_SWE_Pro_evolved_qutebrowser --rollout 3
# ============================================================================

set -euo pipefail
cd "$(dirname "$0")/.."

TS="$(date +%Y%m%d_%H%M%S)"

log() { echo "[$(date +%H:%M:%S)] $*"; }

run_cmd() {
    local desc="$1"; shift
    echo "==========================================================="
    log "$desc"
    echo "==========================================================="
    echo "+ $*"
    "$@"
}

ACTION="${1:?Usage: $0 <evolve|freeze|test> ...}"
shift || true

EVOLVE_CASES="0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19"

ansible_holdout() {
    python3 -c "print(','.join(str(i) for i in range(20, 96)))"
}

qutebrowser_holdout() {
    python3 -c "print(','.join(str(i) for i in range(20, 79)))"
}

# ============================================================================
phase_evolve() {
    local split="${1:?Usage: $0 evolve <ansible|qutebrowser>}"
    local run_id="${TS}_swepro_${split}_evolve"

    log "SWE-bench Pro evolve: split=$split, 20 cases (serial)"

    run_cmd "SWE-bench Pro $split evolve (20 cases)" \
        python3 benchmarks/adapter_swebench_pro.py \
            --split "$split" \
            --cases "$EVOLVE_CASES" \
            --team pool_SWE_Pro \
            --evolve \
            --max-cost 30 \
            --timeout 2400 \
            --workers 1 \
            --run-id "$run_id"

    echo ""
    log "Evolve complete. Next steps:"
    log "  1. bash scripts/run_swebench_pro_paper_experiment.sh freeze ${run_id}_evolve $split"
    log "  2. bash scripts/run_swebench_pro_paper_experiment.sh test $split pool_SWE_Pro_evolved_${split}"
}

# ============================================================================
phase_freeze() {
    local run_id="${1:?Usage: $0 freeze <run_id> <ansible|qutebrowser>}"
    local split="${2:?Usage: $0 freeze <run_id> <ansible|qutebrowser>}"
    local pool_name="pool_SWE_Pro_evolved_${split}"

    bash scripts/freeze_evolved_team.sh "$run_id" latest "$pool_name" --force
}

# ============================================================================
phase_test() {
    local split="${1:?Usage: $0 test <ansible|qutebrowser> <team> [--rollout N]}"
    local team="${2:?Usage: $0 test <split> <team> [--rollout N]}"
    local rollouts=1

    shift 2
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rollout) rollouts="$2"; shift 2 ;;
            *) echo "Unknown arg: $1"; exit 1 ;;
        esac
    done

    local holdout_cases
    case "$split" in
        ansible)     holdout_cases="$(ansible_holdout)" ;;
        qutebrowser) holdout_cases="$(qutebrowser_holdout)" ;;
        *) echo "Unknown split: $split"; exit 1 ;;
    esac

    local holdout_n
    case "$split" in
        ansible)     holdout_n=76 ;;
        qutebrowser) holdout_n=59 ;;
    esac

    log "SWE-bench Pro holdout: split=$split, team=$team, N=$holdout_n, rollouts=$rollouts"

    for r in $(seq 1 "$rollouts"); do
        local run_id="${TS}_swepro_${split}_holdout${holdout_n}_r${r}"
        run_cmd "SWE-bench Pro $split holdout (N=$holdout_n, rollout $r/$rollouts)" \
            python3 benchmarks/adapter_swebench_pro.py \
                --split "$split" \
                --cases "$holdout_cases" \
                --team "$team" \
                --max-cost 30 \
                --timeout 2400 \
                --workers 4 \
                --run-id "$run_id"
    done
}

# ============================================================================
case "$ACTION" in
    evolve) phase_evolve "$@" ;;
    freeze) phase_freeze "$@" ;;
    test)   phase_test "$@" ;;
    *)
        echo "Unknown action: $ACTION"
        echo "Usage: $0 <evolve|freeze|test> ..."
        echo ""
        echo "  evolve <ansible|qutebrowser>           Evolve on first 20 instances"
        echo "  freeze <run_id> <ansible|qutebrowser>  Freeze evolved team"
        echo "  test <ansible|qutebrowser> <team> [--rollout N]"
        echo "                                         Holdout evaluation (avg@N)"
        exit 1
        ;;
esac
