#!/usr/bin/env bash
# 5-GC Production Equality Rule — common production contract runner.
#
# Runs the common GC production-contract suite under ALL FIVE production GC
# backends. A runtime/GC-touching feature is "5-GC production contract pass"
# only when every backend is green here. See the rule in codex-goal-prompt.md
# (G-track) and tests/python/gc_production_contract/README.md.
#
# Performance MAY differ across backends; Python semantics / object safety may
# NOT. This suite asserts the semantic contract, not perf.
#
# Usage:
#   scripts/run_gc_production_contract.sh
#   GC_BACKENDS="0 3 4" scripts/run_gc_production_contract.sh   # subset
set -euo pipefail

SUITE="${GC_CONTRACT_SUITE:-tests/python/gc_production_contract}"
BACKENDS="${GC_BACKENDS:-0 1 2 3 4}"

if [ ! -d "$SUITE" ]; then
  echo "error: contract suite dir not found: $SUITE" >&2
  exit 2
fi

fail=0
for backend in $BACKENDS; do
  echo "=== PCC_GC_BACKEND=$backend production contract ($SUITE) ==="
  if ! PCC_GC_BACKEND="$backend" env -u LC_ALL uv run pytest "$SUITE" -q -n0; then
    echo "!!! backend #$backend FAILED the common production contract" >&2
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "5-GC production contract: FAILED (a production backend is a release blocker)" >&2
  exit 1
fi
echo "5-GC production contract: PASS (all backends ${BACKENDS})"
