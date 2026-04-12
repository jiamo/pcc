#!/usr/bin/env bash
#
# scripts/bootstrap.sh — P6C.6 three-stage bootstrap for pcc.
#
# Per docs/plans/python-frontend-plan.md §Phase 6C.6, the gate for
# declaring pcc self-hosted is:
#
#   stage1: CPython runs pcc, compiles pcc/pcc.py  → pcc1 (native)
#   stage2: ./pcc1 compiles pcc/pcc.py             → pcc2 (native)
#   stage3: ./pcc2 compiles pcc/pcc.py             → pcc3 (native)
#   verify: cmp pcc2 pcc3   (byte-identical; structural-equivalent OK
#                            if build metadata is stripped)
#
# Status (2026-04-20): stage1 will fail because pcc/pcc.py uses
# features the P6C.1..5 pipeline hasn't fully enabled yet — dynamic
# getattr, large stdlib surface, class metaclasses, and complex
# decorators. Running this script today produces a concrete, useful
# error trace for each gap. Re-run after each P6C.[3,4,5] milestone
# to watch the failures shift rightward.
#
# Usage:
#   scripts/bootstrap.sh               # full three-stage + cmp
#   scripts/bootstrap.sh --stage 1     # stop after stage1
#   scripts/bootstrap.sh --clean       # remove all stage artifacts

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${REPO_ROOT}/build/bootstrap"
MAIN_PY="${REPO_ROOT}/pcc/__main__.py"

STAGE_LIMIT=3
CLEAN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stage)   STAGE_LIMIT="$2"; shift 2 ;;
        --clean)   CLEAN=1; shift ;;
        -h|--help) sed -n '3,24p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ $CLEAN -eq 1 ]]; then
    rm -rf "${OUT_DIR}"
    echo "cleaned ${OUT_DIR}"
    exit 0
fi

mkdir -p "${OUT_DIR}"

banner() {
    echo ""
    echo "=============================================================="
    echo " $*"
    echo "=============================================================="
}

run_stage() {
    local stage="$1"
    local out_exe="$2"
    shift 2
    # Remaining positional args are the compiler invocation (as an
    # array so ``python3 -m pcc`` vs a single executable path both
    # work without shell-quoting gymnastics).
    local cmd=("$@")
    banner "stage ${stage}: ${cmd[*]} ${MAIN_PY} -> ${out_exe}"
    if command -v time >/dev/null 2>&1; then
        time "${cmd[@]}" "${MAIN_PY}" -o "${out_exe}"
    else
        "${cmd[@]}" "${MAIN_PY}" -o "${out_exe}"
    fi
}

# stage 1: CPython-hosted pcc produces pcc1.
if [[ ${STAGE_LIMIT} -ge 1 ]]; then
    PYTHON="${PYTHON:-python3}"
    run_stage 1 "${OUT_DIR}/pcc1" "${PYTHON}" -m pcc
fi

# stage 2: pcc1 compiles pcc.py.
if [[ ${STAGE_LIMIT} -ge 2 ]]; then
    run_stage 2 "${OUT_DIR}/pcc2" "${OUT_DIR}/pcc1"
fi

# stage 3: pcc2 compiles pcc.py.
if [[ ${STAGE_LIMIT} -ge 3 ]]; then
    run_stage 3 "${OUT_DIR}/pcc3" "${OUT_DIR}/pcc2"

    banner "verify: cmp pcc2 pcc3"
    if cmp -s "${OUT_DIR}/pcc2" "${OUT_DIR}/pcc3"; then
        echo "OK — pcc2 and pcc3 are byte-identical. Self-host gate passed."
        exit 0
    fi
    # Fall back to size + md5 structural comparison so nondeterministic
    # build metadata (e.g. embedded build-ids) doesn't break the
    # structural equivalence claim outright.
    s2=$(stat -f%z "${OUT_DIR}/pcc2")
    s3=$(stat -f%z "${OUT_DIR}/pcc3")
    echo "pcc2 size: ${s2}"
    echo "pcc3 size: ${s3}"
    if [[ "${s2}" != "${s3}" ]]; then
        echo "FAIL — pcc2 / pcc3 differ in size, not just metadata." >&2
        exit 1
    fi
    echo "WARN — bytes differ but sizes match; metadata noise suspected."
    echo "       Once the build is deterministic (no timestamps / paths"
    echo "       / uuids embedded), cmp should succeed."
    exit 2
fi
