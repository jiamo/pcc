# pcc0 / pcc1 compiler-capability parity intake

Date: 2026-08-29  
Task: `OWN-P0-PCC0-PCC1-COMPILER-CAPABILITY-PARITY`

Current mode-labeled boundary:

- pcc0 is repository Python executed by CPython.  It can use the mature C
  frontend and its pycparser/llvmlite/host dependencies, plus the experimental
  typed-Python frontend.
- pcc1 is the native no-libpython self-host artifact.  Its fixed point proves
  the compiled bootstrap closure, not feature parity with every pcc0 CLI/input
  mode.  Supported typed-Python inputs compile natively; the mature C frontend
  is not yet proven as a pcc1-owned closure.

The new umbrella task closes that distinction for the declared pcc-native
compiler product.  It owns the C-frontend migration and the top-level
capability manifest/parity matrix; complete Python-language/runtime parity
remains implemented by the existing CPython-replacement tasks and becomes a
dependency of the final equality claim.

No current C or arbitrary-Python parity claim is made by this intake receipt.

