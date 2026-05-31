#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

env -u LC_ALL uv run python scripts/check_layer1_ownership.py

env -u LC_ALL uv run pytest \
  tests/python/test_dynamic_import.py \
  tests/python/test_inspect_protocol.py \
  tests/python/test_pickle_copy.py \
  tests/python/test_dataclasses_full.py \
  tests/python/data_model/test_final_language_compiled_acceptance.py \
  tests/python/data_model/test_t4_weakref_native_acceptance.py \
  -q -n0
