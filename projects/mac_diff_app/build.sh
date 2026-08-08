#!/bin/bash
# Build mac_diff_app: Metal render bridge dylib + pcc1-compiled app.
# Pure technical build path — self backend, no libpython.
set -e
cd "$(dirname "$0")"
APP_DIR="$(pwd)"
REPO_ROOT="$(cd ../../ && pwd)"

echo "[1/3] generate + compile Metal render bridge"
cd "$APP_DIR"
uv run python -c \
  "from pcc.kernel_ir.metal_render_surface import write_metal_render_bridge; write_metal_render_bridge('.')"
clang -fobjc-arc -framework Foundation -framework Metal -framework AppKit \
      -framework QuartzCore -dynamiclib pcc_gui_metal_render_bridge.m \
      -o libpcc_gui_metal.dylib

echo "[2/3] locate stage-1 compiler"
PCC1="${PCC1:-}"
if [ -z "$PCC1" ] || [ ! -x "$PCC1" ]; then
  for cand in "${REPO_ROOT%/}/../gui_demo/pcc1" "$REPO_ROOT/build/bootstrap/pcc1" "$REPO_ROOT/pcc1"; do
    if [ -x "$cand" ]; then PCC1="$cand"; break; fi
  done
fi
if [ -z "$PCC1" ] || [ ! -x "$PCC1" ]; then
  echo "pcc1 not found — set PCC1=/path/to/pcc1 or run scripts/bootstrap.sh" >&2
  exit 1
fi

echo "[3/3] compile the app"
cd "$REPO_ROOT"
"$PCC1" --backend self --python-libpython off --ir-scaffold on \
        projects/mac_diff_app/app.py -o "$APP_DIR/mac_diff_app"

echo "built: $APP_DIR/mac_diff_app"
