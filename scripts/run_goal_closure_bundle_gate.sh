#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

bash scripts/run_b1_b6_closure_gate.sh
bash scripts/run_d2_d6_closure_gate.sh
bash scripts/run_final_language_closure_gate.sh

test -f docs/research/c-extension-abi.md
test -f docs/reports/goal-final-evaluation-next-phase.md
test -f docs/investigations/default-backend-verdict.md
test -f docs/investigations/bootstrap-five-gc-matrix.md
