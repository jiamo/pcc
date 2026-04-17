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
# Status (2026-04-23): the script completes stage1/stage2/stage3 on the
# supported macOS arm64 development host. Direct cmp still differs on
# Mach-O code-signature metadata, so verification strips signatures from
# temporary comparison copies before declaring success.
#
# Usage:
#   scripts/bootstrap.sh               # full three-stage + cmp
#                                      # defaults to self on macOS arm64
#   scripts/bootstrap.sh --stage 1     # stop after stage1
#   scripts/bootstrap.sh --backend self --stage 1
#   scripts/bootstrap.sh --backend llvm --stage 1
#   scripts/bootstrap.sh --out-dir build/bootstrap-self --backend self --stage 1
#   scripts/bootstrap.sh --clean       # remove all stage artifacts
#
# Runtime defaults for every stage:
#   PCC_BOOTSTRAP_RUNTIME_CC=pcc
#   PCC_BOOTSTRAP_RUNTIME_HIGH=py
#   PCC_BOOTSTRAP_PYTHON_LIBPYTHON=auto

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${PCC_BOOTSTRAP_OUT_DIR:-${REPO_ROOT}/build/bootstrap}"
MAIN_PY="${REPO_ROOT}/pcc/__main__.py"
BOOTSTRAP_RUNTIME_CC="${PCC_BOOTSTRAP_RUNTIME_CC:-pcc}"
BOOTSTRAP_RUNTIME_HIGH="${PCC_BOOTSTRAP_RUNTIME_HIGH:-py}"
BOOTSTRAP_PYTHON_LIBPYTHON="${PCC_BOOTSTRAP_PYTHON_LIBPYTHON:-auto}"

STAGE_LIMIT=3
CLEAN=0
BACKEND=""
BACKEND_EXPLICIT=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stage)   STAGE_LIMIT="$2"; shift 2 ;;
        --backend) BACKEND="$2"; BACKEND_EXPLICIT=1; shift 2 ;;
        --out-dir) OUT_DIR="$2"; shift 2 ;;
        --clean)   CLEAN=1; shift ;;
        -h|--help) sed -n '3,27p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "${BACKEND}" ]]; then
    if [[ "$(uname)" == "Darwin" ]]; then
        machine="$(uname -m)"
        if [[ "${machine}" == "arm64" || "${machine}" == "aarch64" ]]; then
            BACKEND="self"
        else
            BACKEND="llvm"
        fi
    else
        BACKEND="llvm"
    fi
fi

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
    local backend_args=()
    if [[ -n "${BACKEND}" ]]; then
        backend_args=(--backend "${BACKEND}")
    fi
    local backend_label="${BACKEND}"
    if [[ "${BACKEND_EXPLICIT}" -eq 0 ]]; then
        backend_label="${BACKEND} (default)"
    fi
    local full_cmd=(
        env
        "PCC_RUNTIME_CC=${BOOTSTRAP_RUNTIME_CC}"
        "PCC_RUNTIME_HIGH=${BOOTSTRAP_RUNTIME_HIGH}"
        "${cmd[@]}"
        "${backend_args[@]}"
        --python-libpython "${BOOTSTRAP_PYTHON_LIBPYTHON}"
        "${MAIN_PY}" -o "${out_exe}"
    )
    banner "stage ${stage}: backend ${backend_label}: ${cmd[*]}"
    echo "input: ${MAIN_PY}"
    echo "output: ${out_exe}"
    echo "runtime: PCC_RUNTIME_CC=${BOOTSTRAP_RUNTIME_CC} PCC_RUNTIME_HIGH=${BOOTSTRAP_RUNTIME_HIGH} --python-libpython ${BOOTSTRAP_PYTHON_LIBPYTHON}"
    if command -v time >/dev/null 2>&1; then
        time "${full_cmd[@]}"
    else
        "${full_cmd[@]}"
    fi
}

# stage 1: CPython-hosted pcc produces pcc1.
if [[ ${STAGE_LIMIT} -ge 1 ]]; then
    if command -v uv >/dev/null 2>&1; then
        if [[ -n "${LC_ALL:-}" ]]; then
            run_stage 1 "${OUT_DIR}/pcc1" env -u LC_ALL uv run python -m pcc
        else
            run_stage 1 "${OUT_DIR}/pcc1" uv run python -m pcc
        fi
    else
        PYTHON="${PYTHON:-python3}"
        run_stage 1 "${OUT_DIR}/pcc1" "${PYTHON}" -m pcc
    fi
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
    if [[ "$(uname)" == "Darwin" ]] && command -v codesign >/dev/null 2>&1; then
        tmp2="$(mktemp "${OUT_DIR}/pcc2.compare.XXXXXX")"
        tmp3="$(mktemp "${OUT_DIR}/pcc3.compare.XXXXXX")"
        cp "${OUT_DIR}/pcc2" "${tmp2}"
        cp "${OUT_DIR}/pcc3" "${tmp3}"
        codesign --remove-signature "${tmp2}" >/dev/null 2>&1 || true
        codesign --remove-signature "${tmp3}" >/dev/null 2>&1 || true
        if cmp -s "${tmp2}" "${tmp3}"; then
            rm -f "${tmp2}" "${tmp3}"
            echo "OK — pcc2 and pcc3 differ only by Mach-O code-signature metadata."
            echo "     Signature-normalized copies are byte-identical."
            exit 0
        fi
        rm -f "${tmp2}" "${tmp3}"
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
