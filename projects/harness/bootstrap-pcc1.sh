#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$PROJECT_DIR/../.." && pwd)
OUTPUT_DIR=$PROJECT_DIR/build
BOOTSTRAP_BACKEND=${PCC_HARNESS_BOOTSTRAP_BACKEND:-llvm}

mkdir -p "$OUTPUT_DIR"
cd "$REPO_ROOT"
env -u LC_ALL "$REPO_ROOT/scripts/bootstrap.sh" \
    --stage 1 \
    --backend "$BOOTSTRAP_BACKEND" \
    --out-dir "$OUTPUT_DIR"

env -u LC_ALL uv run python "$PROJECT_DIR/source_provenance.py" \
    "$REPO_ROOT" \
    "$OUTPUT_DIR/pcc1" \
    "$OUTPUT_DIR/pcc1-source.json" \
    "$BOOTSTRAP_BACKEND"

echo "built current-source pcc1: $OUTPUT_DIR/pcc1"
echo "stage-1 construction backend: $BOOTSTRAP_BACKEND"
echo "source identity: $OUTPUT_DIR/pcc1-source.json"
