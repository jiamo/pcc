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
#   PCC_BOOTSTRAP_PYTHON_LIBPYTHON=off
#   PCC_BOOTSTRAP_PYTHON_IR_PASSES=${PCC_PYTHON_IR_PASSES:-off}
#   PCC_BOOTSTRAP_PY_FRONTEND_JOBS=${PCC_PY_FRONTEND_JOBS:-auto} for stage2+
#   PCC_BOOTSTRAP_STAGE1_PY_FRONTEND_JOBS=${PCC_PY_FRONTEND_JOBS:-auto}

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${PCC_BOOTSTRAP_OUT_DIR:-${REPO_ROOT}/build/bootstrap}"
MAIN_PY="${REPO_ROOT}/pcc/__main__.py"
BOOTSTRAP_RUNTIME_CC="${PCC_BOOTSTRAP_RUNTIME_CC:-pcc}"
BOOTSTRAP_RUNTIME_HIGH="${PCC_BOOTSTRAP_RUNTIME_HIGH:-py}"
BOOTSTRAP_PYTHON_LIBPYTHON="${PCC_BOOTSTRAP_PYTHON_LIBPYTHON:-off}"
BOOTSTRAP_PYTHON_IR_PASSES="${PCC_BOOTSTRAP_PYTHON_IR_PASSES:-${PCC_PYTHON_IR_PASSES:-off}}"
BOOTSTRAP_PY_FRONTEND_JOBS="${PCC_BOOTSTRAP_PY_FRONTEND_JOBS:-${PCC_PY_FRONTEND_JOBS:-auto}}"
BOOTSTRAP_STAGE1_PY_FRONTEND_JOBS="${PCC_BOOTSTRAP_STAGE1_PY_FRONTEND_JOBS:-${PCC_PY_FRONTEND_JOBS:-auto}}"
BOOTSTRAP_PROFILE_DIR="${PCC_BOOTSTRAP_PROFILE_DIR:-}"
BOOTSTRAP_STAGE_EXEC_DELAY="${PCC_BOOTSTRAP_STAGE_EXEC_DELAY:-0.10}"

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

now_ms() {
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import time; print(int(time.monotonic() * 1000))'
    else
        echo "$(($(date +%s) * 1000))"
    fi
}

stage_exec_barrier() {
    local out_exe="$1"
    if [[ "${BACKEND}" != "self" ]]; then
        return
    fi
    if [[ "$(uname)" != "Darwin" ]]; then
        return
    fi
    if command -v codesign >/dev/null 2>&1; then
        codesign --verify "${out_exe}" >/dev/null 2>&1 || true
    fi
    cat "${out_exe}" >/dev/null
    if [[ -n "${BOOTSTRAP_STAGE_EXEC_DELAY}" && "${BOOTSTRAP_STAGE_EXEC_DELAY}" != "0" ]]; then
        sleep "${BOOTSTRAP_STAGE_EXEC_DELAY}"
    fi
    "${out_exe}" --help >/dev/null 2>&1
    local smoke_dir
    smoke_dir="$(mktemp -d "${OUT_DIR}/stage-smoke.XXXXXX")"
    local smoke_src="${smoke_dir}/smoke.py"
    local smoke_out="${smoke_dir}/smoke"
    printf 'def main() -> int:\n    return 0\n\nmain()\n' > "${smoke_src}"
    env \
        "PCC_RUNTIME_CC=${BOOTSTRAP_RUNTIME_CC}" \
        "PCC_RUNTIME_HIGH=${BOOTSTRAP_RUNTIME_HIGH}" \
        "${out_exe}" \
        --ir-scaffold=on \
        --backend "${BACKEND}" \
        --python-libpython "${BOOTSTRAP_PYTHON_LIBPYTHON}" \
        "${smoke_src}" -o "${smoke_out}" >/dev/null 2>&1
    local smoke_returncode=$?
    rm -rf "${smoke_dir}"
    return "${smoke_returncode}"
}

write_stage_result_json() {
    local stage="$1"
    local out_exe="$2"
    local compile_elapsed_ms="$3"
    local barrier_elapsed_ms="$4"
    local stage_elapsed_ms="$5"
    local returncode="$6"
    local time_file="$7"
    local barrier_returncode="$8"
    if [[ -z "${BOOTSTRAP_PROFILE_DIR}" ]]; then
        return
    fi
    python3 - "$BOOTSTRAP_PROFILE_DIR/stage${stage}.result.json" \
        "$stage" "$out_exe" "$BACKEND" "$compile_elapsed_ms" \
        "$barrier_elapsed_ms" "$stage_elapsed_ms" "$returncode" \
        "$time_file" "$barrier_returncode" <<'PY'
from __future__ import annotations

import json
import sys

path = sys.argv[1]
stage, output, backend = sys.argv[2:5]
compile_ms, barrier_ms, wall_ms, returncode, time_file = sys.argv[5:10]
barrier_returncode = sys.argv[10]
payload = {
    "schema": "pcc.bootstrap_stage_result.v1",
    "stage": int(stage),
    "output": output,
    "backend": backend,
    "compile_wall_ms": int(compile_ms),
    "publish_barrier_ms": int(barrier_ms),
    "wall_ms": int(wall_ms),
    "returncode": int(returncode),
    "publish_barrier_returncode": int(barrier_returncode),
}
if time_file:
    try:
        with open(time_file, "r", encoding="utf-8") as f:
            for raw in f:
                key, sep, value = raw.strip().partition("=")
                if sep != "=":
                    continue
                if key == "user_s":
                    payload["compile_user_ms"] = int(float(value) * 1000)
                elif key == "sys_s":
                    payload["compile_sys_ms"] = int(float(value) * 1000)
                elif key == "real_s":
                    payload["compile_time_real_ms"] = int(float(value) * 1000)
    except OSError:
        pass
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2, sort_keys=True)
    f.write("\n")
PY
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
    if [[ -n "${BOOTSTRAP_PROFILE_DIR}" ]]; then
        mkdir -p "${BOOTSTRAP_PROFILE_DIR}"
    fi
    # Never let a failed/short-circuited compile leave a previous run's stage
    # binary in place. Without this, stage3 can accidentally execute a stale
    # pcc2 from the shared pytest/bootstrap directory.
    rm -f "${out_exe}" "${out_exe}.tmp"

    local backend_label="${BACKEND}"
    local frontend_jobs="${BOOTSTRAP_PY_FRONTEND_JOBS}"
    if [[ "${stage}" == "1" ]]; then
        frontend_jobs="${BOOTSTRAP_STAGE1_PY_FRONTEND_JOBS}"
    fi
    if [[ "${BACKEND_EXPLICIT}" -eq 0 ]]; then
        backend_label="${BACKEND} (default)"
    fi
    local full_cmd=(
        env
        "PCC_RUNTIME_CC=${BOOTSTRAP_RUNTIME_CC}"
        "PCC_RUNTIME_HIGH=${BOOTSTRAP_RUNTIME_HIGH}"
        "PCC_PYTHON_IR_PASSES=${BOOTSTRAP_PYTHON_IR_PASSES}"
        "PCC_PY_FRONTEND_JOBS=${frontend_jobs}"
        "${cmd[@]}"
    )
    if [[ -n "${BOOTSTRAP_PROFILE_DIR}" ]]; then
        full_cmd+=(--profile-json "${BOOTSTRAP_PROFILE_DIR}/stage${stage}.json")
    fi
    full_cmd+=(
        "${backend_args[@]}"
        --python-libpython "${BOOTSTRAP_PYTHON_LIBPYTHON}"
        "${MAIN_PY}" -o "${out_exe}"
    )
    banner "stage ${stage}: backend ${backend_label}: ${cmd[*]}"
    echo "input: ${MAIN_PY}"
    echo "output: ${out_exe}"
    echo "runtime: PCC_RUNTIME_CC=${BOOTSTRAP_RUNTIME_CC} PCC_RUNTIME_HIGH=${BOOTSTRAP_RUNTIME_HIGH} PCC_PYTHON_IR_PASSES=${BOOTSTRAP_PYTHON_IR_PASSES} PCC_PY_FRONTEND_JOBS=${frontend_jobs} --python-libpython ${BOOTSTRAP_PYTHON_LIBPYTHON}"
    if [[ -n "${BOOTSTRAP_PROFILE_DIR}" ]]; then
        echo "profile: ${BOOTSTRAP_PROFILE_DIR}/stage${stage}.json"
    fi
    local stage_start_ms
    local compile_start_ms
    local compile_end_ms
    local compile_elapsed_ms
    local barrier_start_ms
    local barrier_end_ms
    local barrier_elapsed_ms
    local stage_end_ms
    local stage_elapsed_ms
    local stage_returncode
    local barrier_returncode=0
    local stage_time_file=""
    if [[ -n "${BOOTSTRAP_PROFILE_DIR}" ]]; then
        stage_time_file="${BOOTSTRAP_PROFILE_DIR}/stage${stage}.time"
    fi
    stage_start_ms="$(now_ms)"
    compile_start_ms="${stage_start_ms}"
    set +e
    if command -v time >/dev/null 2>&1; then
        if [[ -n "${stage_time_file}" ]]; then
            { TIMEFORMAT=$'real_s=%3R\nuser_s=%3U\nsys_s=%3S'; time "${full_cmd[@]}" 2>&3; } 3>&2 2> "${stage_time_file}"
        else
            time "${full_cmd[@]}"
        fi
        stage_returncode=$?
    else
        "${full_cmd[@]}"
        stage_returncode=$?
    fi
    set -e
    compile_end_ms="$(now_ms)"
    compile_elapsed_ms=$((compile_end_ms - compile_start_ms))
    barrier_start_ms="${compile_end_ms}"
    if [[ ${stage_returncode} -eq 0 ]]; then
        if [[ ! -s "${out_exe}" || ! -x "${out_exe}" ]]; then
            echo "FAIL — stage ${stage} did not produce executable ${out_exe}; refusing stale stage artifact." >&2
            stage_returncode=127
        fi
    fi
    if [[ ${stage_returncode} -eq 0 ]]; then
        set +e
        stage_exec_barrier "${out_exe}"
        barrier_returncode=$?
        set -e
        if [[ ${barrier_returncode} -ne 0 ]]; then
            stage_returncode="${barrier_returncode}"
        fi
    fi
    barrier_end_ms="$(now_ms)"
    barrier_elapsed_ms=$((barrier_end_ms - barrier_start_ms))
    stage_end_ms="$(now_ms)"
    stage_elapsed_ms=$((stage_end_ms - stage_start_ms))
    write_stage_result_json \
        "${stage}" "${out_exe}" "${compile_elapsed_ms}" \
        "${barrier_elapsed_ms}" "${stage_elapsed_ms}" "${stage_returncode}" \
        "${stage_time_file}" "${barrier_returncode}"
    echo "PCC_BOOTSTRAP_STAGE_RESULT stage=${stage} elapsed_ms=${stage_elapsed_ms} output=${out_exe}"
    if [[ ${stage_returncode} -ne 0 ]]; then
        exit "${stage_returncode}"
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
