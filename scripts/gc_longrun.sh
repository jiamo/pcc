#!/usr/bin/env bash
# G-P3-LONGRUN manual tier (docs/plans/gc-longrun-benchmark-plan.md).
# Compiles the long-run workloads once and runs the MINUTES-scale tier
# on every GC backend, writing CSV time series per (workload, backend)
# under an output directory. Never invoked by default pytest.
#
# Usage: scripts/gc_longrun.sh [outdir] [churn_rounds] [gs_cycles]
set -euo pipefail

OUTDIR="${1:-/tmp/pcc-gc-longrun-$(date +%Y%m%d-%H%M%S)}"
CHURN_ROUNDS="${2:-200000}"
GS_CYCLES="${3:-4000}"
FIN_ROUNDS="${4:-100000}"
PM_ROUNDS="${5:-200000}"
mkdir -p "$OUTDIR"

echo "[gc_longrun] building workloads ..."
env -u LC_ALL uv run pcc --python-libpython=off --ir-scaffold=on \
  --backend self benchmarks/python/longrun_churn.py -o "$OUTDIR/churn"
env -u LC_ALL uv run pcc --python-libpython=off --ir-scaffold=on \
  --backend self benchmarks/python/longrun_growshrink.py -o "$OUTDIR/growshrink"
env -u LC_ALL uv run pcc --python-libpython=off --ir-scaffold=on \
  --backend self benchmarks/python/longrun_finalizers.py -o "$OUTDIR/finalizers"
env -u LC_ALL uv run pcc --python-libpython=off --ir-scaffold=on \
  --backend self benchmarks/python/longrun_pointer_mutator.py -o "$OUTDIR/pointer_mutator"

# A crashing (workload, backend) pair must not abort the rest of the
# matrix: record per-series exit codes in status.tsv and continue.
STATUS="$OUTDIR/status.tsv"
: > "$STATUS"
run_series() {
  local workload="$1" backend="$2" rounds="$3"
  echo "[gc_longrun] $workload backend=$backend rounds=$rounds"
  local rc=0
  PCC_GC_BACKEND=$backend "$OUTDIR/$workload" "$rounds" \
    > "$OUTDIR/$workload.gc$backend.csv" || rc=$?
  printf '%s\tgc%s\texit=%s\n' "$workload" "$backend" "$rc" >> "$STATUS"
}

for backend in 0 1 2 3 4; do
  run_series churn "$backend" "$CHURN_ROUNDS"
  run_series growshrink "$backend" "$GS_CYCLES"
  run_series finalizers "$backend" "$FIN_ROUNDS"
  run_series pointer_mutator "$backend" "$PM_ROUNDS"
done

echo "[gc_longrun] done — series under $OUTDIR (exit codes: $STATUS)"
