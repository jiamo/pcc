# Investigation: stage1 self-backend ir-scaffold segfault

## Status
active

## Problem Description
User-reported command sequence:

```bash
uv run pcc --ir-scaffold=on --python-libpython=off --backend self pcc/__main__.py -o pcc1
./pcc1 --ir-scaffold=on --python-libpython=off --backend self pcc/__main__.py -o pcc2
```

The second command crashes with:

```text
zsh: segmentation fault  ./pcc1 --ir-scaffold=on --python-libpython=off --backend self pcc/__main__.py
```

Reduction for this investigation: reproduce the stage1 binary crash when it is used to build stage2 with `--ir-scaffold=on`, `--python-libpython=off`, and `--backend self`.

## Repro
From `/Users/jiamo/my/pcc`, with the user-provided `pcc1` binary:

```bash
perl -e 'alarm shift; exec @ARGV' 30 ./pcc1 --ir-scaffold=on --python-libpython=off --backend self pcc/__main__.py -o /tmp/pcc2_repro
```

Observed 2026-05-09: the process exits via segmentation fault before producing a Python traceback.

## Test [CONFIRMED]
The repro command above is the current gating test. It has been run locally and observed to crash.

Focused regression added:

```bash
env -u LC_ALL -u LC_CTYPE uv run pytest 'tests/test_ir_scaffold_dispatch.py::test_dyn_append_prefers_native_list_over_unrelated_class_method' -q -n0
```

Observed before the fix: fails because the generated IR contains a direct call to `@user_test_module_Block_append` for a `DynType` receiver's `.append(...)` call, instead of routing through `@py_list_append`.

## Proposals
- No.1 Capture native backtrace and patch the smallest runtime/lowering defect     [pending]

## No.1 Capture native backtrace and patch the smallest runtime/lowering defect
### Code Change
Pending. Backtrace shows the segfault happens in `py_str_strip`, called by `pcc.llvm_capi.ir._opname_of` after `pcc.llvm_capi.ir.Block.append` receives a non-string `line` argument.

The minimized source-level cause is a method dispatch error in `_emit_method_call`: a `DynType` receiver call `active_excs.append(handler_exc)` is captured by the closed-world "any class declaring the method" fallback before the later DynType native-list method path can run. Because `pcc.llvm_capi.ir.Block` declares `append`, stage1 emits a direct call to `Block.append(handler_exc)`, passing an `ir.Value` where `Block.append` expects a string instruction line.

### CONFIRMED|DENIED|DENIED BY USER
Pending.
