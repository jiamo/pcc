#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

env -u LC_ALL uv run pytest \
  tests/python/data_model/test_d2_d6_compiled_acceptance.py \
  tests/python/data_model/test_d2_d6_runtime_wiring_regression.py \
  tests/python/test_generator_protocol.py \
  tests/python/test_async_await.py \
  tests/python/test_context_manager_full.py \
  tests/python/test_protocol_edges.py \
  tests/python/test_format_protocol.py \
  -q -n0
