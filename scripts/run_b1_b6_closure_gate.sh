#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

env -u LC_ALL uv run pytest \
  tests/python/data_model/test_b1_b6_compiled_acceptance.py \
  tests/python/data_model/test_b1_b6_runtime_wiring_regression.py \
  tests/python/data_model/test_bytes_literal.py \
  tests/python/test_runtime_type_builtin_native.py \
  tests/python/data_model/test_classvar_runtime.py \
  tests/python/data_model/test_user_dunder_runtime.py \
  tests/python/data_model/test_exception_chaining_runtime.py \
  tests/python/data_model/test_call_splat_runtime.py \
  -q -n0
