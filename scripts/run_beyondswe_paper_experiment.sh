#!/usr/bin/env bash
# ============================================================================
# run_beyondswe_paper_experiment.sh
#
#
#   CrossRepo:  200 total = 20 evolve + 180 holdout
#   DepMigrate: 178 total = 20 evolve + 158 holdout
#
#
# Phases:
#
# Usage:
#   bash scripts/run_beyondswe_paper_experiment.sh evolve crossrepo
#   bash scripts/run_beyondswe_paper_experiment.sh evolve depmigrate
#   bash scripts/run_beyondswe_paper_experiment.sh freeze <run_id> crossrepo
#   bash scripts/run_beyondswe_paper_experiment.sh test crossrepo pool_BeyondSWE_evolved_cr
#   bash scripts/run_beyondswe_paper_experiment.sh test depmigrate pool_BeyondSWE_evolved_dm --rollout 3
# ============================================================================

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${META_TEAM_PYTHON:-python3}"
ENV_FILE="${META_TEAM_ENV_FILE:-$PWD/apiconfig/beyond.env}"

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

ACTION="${1:?Usage: $0 <evolve|freeze|test|dry-run> ...}"
shift || true

# the remaining instances form the Evaluation set"
EVOLVE_CASES="0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19"

crossrepo_holdout() {
    # CrossRepo: 200 total, idx 20-199 = 180 holdout
    "${PYTHON_BIN}" -c "print(','.join(str(i) for i in range(20, 200)))"
}

depmigrate_holdout() {
    # DepMigrate: 178 total, idx 20-177 = 158 holdout
    "${PYTHON_BIN}" -c "print(','.join(str(i) for i in range(20, 178)))"
}

# ============================================================================
# ============================================================================
phase_evolve() {
    local split="${1:?Usage: $0 evolve <crossrepo|depmigrate>}"
    local run_id="${BEYONDSWE_RUN_ID:-${TS}_bswe_${split}}"
    local run_dir="${run_id}"
    local effective_timeout="${BEYONDSWE_EFFECTIVE_TIMEOUT:-2400}"
    local wall_timeout="${BEYONDSWE_WALL_TIMEOUT:-3600}"
    local command_args=(
        env META_TEAM_ENV_FILE="${ENV_FILE}"
        "${PYTHON_BIN}" benchmarks/adapter_beyondswe.py
        --split "$split"
        --cases "$EVOLVE_CASES"
        --team pool_BeyondSWE
        --evolve
        --max-cost 30
        --timeout "${wall_timeout}"
        --effective-timeout "${effective_timeout}"
        --workers 1
        --run-id "$run_id"
    )
    if [[ "${run_dir}" != *_evolve ]]; then
        run_dir="${run_dir}_evolve"
    fi
    if [[ "${BEYONDSWE_RESUME:-0}" == "1" ]]; then
        command_args+=(--resume)
        echo "=== RESUMING EXISTING EVOLVE RUN: ${run_dir} ==="
    fi
    log "BeyondSWE evolve: split=$split, 20 cases (serial)"
    log "Team: pool_BeyondSWE"
    log "Run ID: $run_id"

    env META_TEAM_ENV_FILE="${ENV_FILE}" \
        "${PYTHON_BIN}" scripts/preflight_beyondswe.py --split "${split}"
    run_cmd "BeyondSWE $split evolve (20 cases, serial)" \
        "${command_args[@]}"

    echo ""
    log "Evolve complete. Next steps:"
    log "  1. bash scripts/freeze_evolved_team.sh ${run_dir} latest pool_BeyondSWE_evolved_${split}"
    log "  2. bash scripts/run_beyondswe_paper_experiment.sh test $split pool_BeyondSWE_evolved_${split}"
}

# ============================================================================
# ============================================================================
phase_freeze() {
    local run_id="${1:?Usage: $0 freeze <run_id> <crossrepo|depmigrate>}"
    local split="${2:?Usage: $0 freeze <run_id> <crossrepo|depmigrate>}"
    local pool_name="pool_BeyondSWE_evolved_${split}"

    bash scripts/freeze_evolved_team.sh "$run_id" latest "$pool_name" --force
}

# ============================================================================
# ============================================================================
phase_test() {
    local split="${1:?Usage: $0 test <crossrepo|depmigrate> <team> [--rollout N]}"
    local team="${2:?Usage: $0 test <split> <team> [--rollout N]}"
    local rollouts=1

    shift 2
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rollout) rollouts="${2:?--rollout requires a number}"; shift 2 ;;
            *) echo "Unknown arg: $1"; exit 1 ;;
        esac
    done

    local holdout_cases
    case "$split" in
        crossrepo)  holdout_cases="$(crossrepo_holdout)" ;;
        depmigrate) holdout_cases="$(depmigrate_holdout)" ;;
        *) echo "Unknown split: $split"; exit 1 ;;
    esac

    local holdout_n
    case "$split" in
        crossrepo)  holdout_n=180 ;;
        depmigrate) holdout_n=158 ;;
    esac

    log "BeyondSWE holdout test: split=$split, team=$team, N=$holdout_n, rollouts=$rollouts"

    for r in $(seq 1 "$rollouts"); do
        local run_id="${TS}_bswe_${split}_holdout${holdout_n}_r${r}"
        run_cmd "BeyondSWE $split holdout (N=$holdout_n, rollout $r/$rollouts)" \
            env META_TEAM_ENV_FILE="${ENV_FILE}" \
            "${PYTHON_BIN}" benchmarks/adapter_beyondswe.py \
                --split "$split" \
                --cases "$holdout_cases" \
                --team "$team" \
                --max-cost 30 \
                --timeout 1800 \
                --effective-timeout 1800 \
                --workers "${BEYONDSWE_TEST_WORKERS:-3}" \
                --run-id "$run_id"
    done
}

# ============================================================================
# ============================================================================
phase_dryrun() {
    DRY_RUN=1
    echo "=== BeyondSWE Paper Experiment — Dry Run ==="
    echo ""
    phase_evolve crossrepo
    echo ""
    phase_evolve depmigrate
    echo ""
    echo "--- After evolve + freeze ---"
    echo ""
    phase_test crossrepo pool_BeyondSWE_evolved_crossrepo --rollout 3
    echo ""
    phase_test depmigrate pool_BeyondSWE_evolved_depmigrate --rollout 3
}

case "$ACTION" in
    evolve)   phase_evolve "$@" ;;
    freeze)   phase_freeze "$@" ;;
    test)     phase_test "$@" ;;
    dry-run)  phase_dryrun ;;
    *)
        cat <<EOF
Usage: $0 <phase> [options]

Phases:
  evolve <crossrepo|depmigrate>
      Evolve on first 20 instances (serial, --workers 1).

  freeze <run_id> <crossrepo|depmigrate>
      Freeze the evolved team to agents/pool_BeyondSWE_evolved_<split>/.

  test <crossrepo|depmigrate> <team> [--rollout N]
      Evaluate on holdout set (180 or 158 instances).
      --rollout N runs N independent rollouts for avg@N.

  dry-run
      Show all commands without executing.

Paper Table 7 splits:
  CrossRepo:  200 total → 20 evolve + 180 holdout
  DepMigrate: 178 total → 20 evolve + 158 holdout

Environment:
  DRY_RUN=1            Only print commands
  META_TEAM_MODEL=...  Override LLM model (default: claude-sonnet-4.6)
  META_TEAM_ENV_FILE=... API config (default: apiconfig/beyond.env)
EOF
        ;;
esac
