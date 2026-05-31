# Investigation: `pcc/__main__.py` 4 → 0 fallback via `cli_bootstrap` static export

## Status
resolved

## Problem Description

`pcc/__main__.py` is the 2-line entry-point module:

```python
from pcc.cli_bootstrap import bootstrap_cli_sys_argv_exit

if __name__ == "__main__":
    bootstrap_cli_sys_argv_exit()
```

Its standalone per-module compile (under `ir_scaffold_mode=on`)
produced 4 residual `py_cpy_*` calls:

```
call void () @py_cpy_ensure_init()
call ptr (ptr) @py_cpy_import(ptr %.3)
call ptr (ptr, ptr) @py_cpy_getattr(ptr %cpy.fromimport.pcc.cli_bootstrap.1.4, ptr %.5)
call ptr (ptr) @py_cpy_call_noargs(ptr %cpy.fn.bootstrap_cli_sys_argv_exit.8.9)
```

Two reasons:
1. `pcc.cli_bootstrap` was listed in
   `_PCC_FRONTEND_STATIC_NATIVE_MODULES` (consumer allowlist) but had
   no entry in `_PCC_FRONTEND_STATIC_NATIVE_EXPORTS`, so the
   `bootstrap_cli_sys_argv_exit` symbol couldn't be bound natively.
2. `pcc.__main__` itself was missing from both the consumer
   allowlist and the `_default_native_module_exports` inline check,
   so the standalone compile didn't even consult the static table.

## Repro

```bash
env -u LC_ALL uv run python -c "
import sys; sys.path.insert(0, '.')
from pcc.py_frontend.codegen import layer1
from pcc.py_frontend import type_infer
from pcc.parse.py_lift import parse_and_lift
src = 'pcc/__main__.py'
ast_mod = parse_and_lift(open(src).read(), src, 'pcc.__main__')
typed = type_infer.infer_module(ast_mod)
cg = layer1.L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode='on')
import re
print(len(re.findall(r'\bcall [^\n]*@py_cpy_[a-z_]+', str(cg.generate(typed)))))
"
```

Pre-fix prints `4`; post-fix prints `0`.

## Test [CONFIRMED]

`tests/python/test_fallback_baseline.py` +
`test_ir_py_fallback_baseline.py` +
`test_bootstrap_gate_baseline.py` all pass after baseline recapture.
Full 3-stage self bootstrap passes in 52.1s.

## Proposals

- No.1 Add `pcc.cli_bootstrap` EXPORTS entry + register
  `pcc.__main__` in the consumer allowlist                    [CONFIRMED]

## No.1 cli_bootstrap export + __main__ allowlist
### Code Change

`pcc/py_frontend/codegen/layer1_support.py`:

```python
"pcc.cli_bootstrap": {
    "bootstrap_cli_sys_argv_exit": _function_export(
        ("none",),
        (),
        (),
    ),
},
```

Plus `pcc.__main__` added to:
- `_PCC_FRONTEND_STATIC_NATIVE_MODULES` frozenset.
- `_default_native_module_exports` inline check.

`tests/fallback_baseline.json`:
- `on_mode_per_module["pcc.__main__"]`: 4 → 0.
- `per_module["pcc.__main__"]`: 4 → 0.
- Added log entry `2026-05-28-pcc-main-cli-bootstrap-export`.

### CONFIRMED
- Standalone compile of `pcc/__main__.py` emits 0 `py_cpy_*` calls.
- Fallback + bootstrap-gate baselines pass: 17 / 17, 4 skipped.
- 3-stage self bootstrap passes in 52.1s.

### Note on return-type tag casing
Initial attempt used `_function_export(("None",), (), ())` and hit
`TypeError: unknown export type descriptor tag: 'None'`. The valid
tag is the lowercase `"none"` per
`pcc/py_frontend/export_meta.py::_decode_type_uncached`. Other
falsy / unit-type ABIs to watch: `"int"`, `"float"`, `"bool"`,
`"str"`, `"bytes"`, `"list"`, `"dict"`, `"tuple"`, etc. The
casing matters and there's no auto-canonicalization at the
descriptor boundary.

## Report
Landed. Second B-P0-PKG fallback-shrink slice of the session
(after `py_lex` 10 → 0 for `pcc.parse.py_parse`). The
`pcc.__main__` entry now has zero standalone `py_cpy_*` calls,
locked at zero in the baseline JSON.
