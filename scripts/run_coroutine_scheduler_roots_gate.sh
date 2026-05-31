#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

env -u LC_ALL uv run pytest \
  tests/python/test_gc_coroutine_roots.py \
  tests/python/test_gc_coroutine_scheduler_roots_production.py \
  -q -n0
