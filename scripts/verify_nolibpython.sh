#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CC_BIN="${CC:-cc}"
TMPDIR="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "$TMPDIR/pcc-nolibpython.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

make -C "$ROOT/pcc/py_runtime" libpy_runtime.a libpy_runtime_pcc_py.a >/dev/null

cat > "$WORK/nolibpython_smoke.c" <<'C'
#include "py_runtime.h"

int main(void) {
    PyObject *x = py_int_from_i64(123);
    if (x == 0) return 10;
    py_print(x);
    py_decref(x);
    if (pcc_threads_enabled() < 0) return 11;
    if (pcc_refcount_strategy() < 0) return 12;
    if (pcc_stop_the_world() != 0) return 13;
    if (pcc_resume_world() != 0) return 14;
    return 0;
}
C

"$CC_BIN" -std=c11 \
  -I"$ROOT/pcc/py_runtime/include" \
  "$WORK/nolibpython_smoke.c" \
  "$ROOT/pcc/py_runtime/libpy_runtime.a" \
  -lm -ldl -lpthread \
  -o "$WORK/nolibpython_smoke"

OUT="$($WORK/nolibpython_smoke)"
if [[ "$OUT" != "123" ]]; then
  echo "unexpected smoke output: $OUT" >&2
  exit 20
fi

if command -v ldd >/dev/null 2>&1; then
  if ldd "$WORK/nolibpython_smoke" | grep -i python >&2; then
    echo "binary links libpython according to ldd" >&2
    exit 21
  fi
fi

if command -v readelf >/dev/null 2>&1; then
  if readelf -d "$WORK/nolibpython_smoke" | grep -i python >&2; then
    echo "binary NEEDED list contains libpython" >&2
    exit 22
  fi
fi

if command -v nm >/dev/null 2>&1; then
  if nm -D "$WORK/nolibpython_smoke" 2>/dev/null | grep -E '(^|[[:space:]])(_?Py[A-Z_a-z0-9]+|Py[A-Z_a-z0-9]+)$' >&2; then
    echo "binary exports/imports CPython C-API symbols" >&2
    exit 23
  fi
fi

echo "no-libpython smoke passed: $WORK/nolibpython_smoke"
