#!/usr/bin/env bash
# freeze_evolved_team.sh
#
#
#
# Usage:
#   bash scripts/freeze_evolved_team.sh <run_id> <version> <new_team_name> [--force]
#
# Examples:
#   bash scripts/freeze_evolved_team.sh \
#     20260503_120000_locabench_evolve_96k_evolve \
#     v016 \
#     pool_LOCAbench_evolved_final
#

set -euo pipefail

if [[ $# -lt 3 ]]; then
    cat <<EOF
Usage: $0 <run_id> <version> <new_team_name> [--force]

Arguments:
  run_id          Evolution run ID, e.g. 20260503_120000_locabench_evolve_96k_evolve
  version         Team version to freeze, e.g. v016 (use 'latest' to auto-pick)
  new_team_name   Destination team name under agents/, e.g. pool_LOCAbench_evolved_final
  --force         (optional) Overwrite existing agents/<new_team_name>/
EOF
    exit 1
fi

RUN_ID="$1"
VERSION="$2"
NEW_TEAM_NAME="$3"
FORCE_FLAG="${4:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNS_DIR="${ROOT}/runs"
AGENTS_DIR="${ROOT}/agents"

RUN_DIR="${RUNS_DIR}/${RUN_ID}"
TEAM_DIR="${RUN_DIR}/team"
DEST_DIR="${AGENTS_DIR}/${NEW_TEAM_NAME}"

if [[ ! -d "${TEAM_DIR}" ]]; then
    echo "[error] run/team dir not found: ${TEAM_DIR}" >&2
    exit 2
fi

if [[ "${VERSION}" == "latest" ]]; then
    VERSION=$(
        ls "${TEAM_DIR}" \
        | grep -E '^v[0-9]+$' \
        | sort \
        | tail -n 1
    )
    if [[ -z "${VERSION}" ]]; then
        echo "[error] no vNNN versions found in ${TEAM_DIR}" >&2
        exit 3
    fi
    echo "[info] auto-picked latest version: ${VERSION}"
fi

SRC_DIR="${TEAM_DIR}/${VERSION}"
if [[ ! -d "${SRC_DIR}" ]]; then
    echo "[error] team version not found: ${SRC_DIR}" >&2
    echo "[info] available versions:" >&2
    ls "${TEAM_DIR}" | grep -E '^v[0-9]+' | sed 's/^/       /' >&2
    exit 4
fi

if [[ -d "${DEST_DIR}" && "${FORCE_FLAG}" != "--force" ]]; then
    echo "[error] destination already exists: ${DEST_DIR}" >&2
    echo "[info] pass --force to overwrite" >&2
    exit 5
fi

if [[ -d "${DEST_DIR}" ]]; then
    echo "[warn] removing existing ${DEST_DIR}"
    rm -rf "${DEST_DIR}"
fi

echo "[copy] ${SRC_DIR} -> ${DEST_DIR}"
cp -r "${SRC_DIR}" "${DEST_DIR}"

find "${DEST_DIR}" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

cat > "${DEST_DIR}/FROZEN_FROM.md" <<EOF
# Frozen Team Snapshot

- **Frozen at**: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
- **Source run**: \`runs/${RUN_ID}/team/${VERSION}/\`
- **Frozen to**: \`agents/${NEW_TEAM_NAME}/\`

Do NOT edit this directory by hand; it is a reproducibility snapshot.
If you want to re-evolve, start a new evolve run and freeze a new version.
EOF

echo ""
echo "[done] Frozen team: ${NEW_TEAM_NAME}"
echo "       agents/${NEW_TEAM_NAME}/"
echo ""
echo "Contents:"
find "${DEST_DIR}" -maxdepth 2 -type d | sort | sed 's#'"${ROOT}"'/#  #'
echo ""
echo "Next step: run Evaluation with --team ${NEW_TEAM_NAME}"
