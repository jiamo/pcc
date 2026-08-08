#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$PROJECT_DIR/../.." && pwd)
OUTPUT_DIR=$PROJECT_DIR/build
OUTPUT=$OUTPUT_DIR/harness-core
BRIDGE_SOURCE=$OUTPUT_DIR/pcc_gui_metal_render_bridge.m
BRIDGE=$OUTPUT_DIR/libpcc_gui_metal.dylib
BRIDGE_GENERATOR=$REPO_ROOT/pcc/kernel_ir/metal_render_surface.py

if [ -n "${PCC1:-}" ]; then
    PCC1_BIN=$PCC1
elif [ -x "$OUTPUT_DIR/pcc1" ]; then
    PCC1_BIN=$OUTPUT_DIR/pcc1
elif [ -x "$REPO_ROOT/build/bootstrap-self/pcc1" ]; then
    PCC1_BIN=$REPO_ROOT/build/bootstrap-self/pcc1
elif [ -x "$REPO_ROOT/build/bootstrap-pytest-shared-stage1/pcc1" ]; then
    PCC1_BIN=$REPO_ROOT/build/bootstrap-pytest-shared-stage1/pcc1
else
    echo "no verified pcc1 found; run projects/harness/bootstrap-pcc1.sh" >&2
    exit 1
fi

if [ ! -x "$PCC1_BIN" ]; then
    echo "pcc1 not found: $PCC1_BIN" >&2
    exit 1
fi

if [ "$PCC1_BIN" = "$OUTPUT_DIR/pcc1" ]; then
    env -u LC_ALL uv run python "$PROJECT_DIR/source_provenance.py" \
        --verify "$REPO_ROOT" "$PCC1_BIN" "$OUTPUT_DIR/pcc1-source.json"
fi

mkdir -p "$OUTPUT_DIR"

if [ ! -f "$BRIDGE" ] || [ "$BRIDGE_GENERATOR" -nt "$BRIDGE" ]; then
    env -u LC_ALL uv run python -c \
        'import sys; from pcc.kernel_ir.metal_render_surface import write_metal_render_bridge; write_metal_render_bridge(sys.argv[1])' \
        "$OUTPUT_DIR"
    clang -fobjc-arc \
        -framework Foundation \
        -framework Metal \
        -framework AppKit \
        -framework QuartzCore \
        -dynamiclib "$BRIDGE_SOURCE" \
        -o "$BRIDGE"
fi

cd "$REPO_ROOT"
"$PCC1_BIN" --backend self --python-libpython off --ir-scaffold on \
    projects/harness/app.py -o "$OUTPUT"

echo "built: $OUTPUT"
echo "compiler: $PCC1_BIN"
echo "gui bridge: $BRIDGE"
