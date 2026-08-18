# Investigation: L1CodeGen mixin state outside the host contract aliases host slots under pcc1

## Status

fixed (contract completed, ratchet test added); pcc1 confirmation recorded in
`docs/goal/evidence/PERF-P0-INLINE-ERROR-EDGE-DATA-PLANE/003-shared-frame-landings-classgen-17pct.md`

## Problem Description

In a pcc1-compiled stage, `ExceptionLoweringMixin._active_handler_exception_for_current_function`
read `self._active_handler_excs` as a **dict with 401 entries** (every other
read saw the expected list), and `active_stack[-1]` raised `KeyError(-1)`.
With the historical silent-NULL `py_obj_getitem` the same corrupted read
produced NULL, `isinstance(entry, tuple)` rejected it and compilation went on
with wrong implicit-exception-context state; the raising dyn subscript
(`BUG-P0-DYN-SUBSCRIPT-SILENT-NULL`) made it fatal.  Host codegen of the same
module is correct.

## Repro

Class_gen worker replay with a pcc1 built from the current source:

```text
build/inline-edge-stage1-v{1..7}/pcc1 --pcc-python-multi-codegen-worker <manifest for item 87>
ERR  codegen[pcc.py_frontend.codegen.class_gen]: KeyError: -1
```

`PCC_DEBUG_CODEGEN_PHASES=1` with a re-raise frame trail
(`_codegen_trace_dump`) names the read; a type/id probe shows
`type=dict len=401` at the failing read and `type=list` elsewhere.  A
scratch-snapshot bisect with `subscript_lowering.py` reverted to HEAD still
shows the dict reads (worker "succeeds" only because NULL was tolerated).

## Test [CONFIRMED]

Ground truth from a host multi-module text IR of the stage1 closure
(`scripts/probe_stage1_closure.py::_try_full_multi_compile`):

```text
py_class_new(L1CodeGen, ..., 177)                       ; 177 contract fields
self._active_handler_excs  -> py_instance_get_field(self, 2)   ; contract slot 2 everywhere
DebugInfoLoweringMixin._di_init:
  self.module              -> py_instance_get_field(self, 175) ; contract slot (host-typed self)
  self._di_file            -> py_instance_set_field(self, 0)   ; mixin's OWN field index
  self._di_compile_unit    -> py_instance_set_field(self, 1)
  self._di_scope           -> py_instance_set_field(self, 2)   ; aliases _active_handler_excs
  self._di_subprograms     -> py_instance_set_field(self, 3)   ; aliases _ast_body
```

Mechanism: a host mixin's `self` is typed as the L1CodeGen host, so a
contract attribute resolves to its contract slot; an attribute the mixin
writes that the contract does **not** list falls back to the mixin's own
`ClassInfo.field_names` index and is stored at that number on the shared
L1CodeGen instance.  `DebugInfoLoweringMixin` (HEAD c6c78f06, 2026-09-02) added
four such fields, so host slots 0-3 were overwritten on every module init;
`_di_subprograms` (one entry per lowered function) is the 401-entry dict.
The same survey found five older mixins with 9 more uncontracted attributes
(`_cpy_init_emitted_fns`, `_native_extension_star_module_env`,
`_last_call_arg_owned_temp`, `_module_del_target_names`,
`_native_class_export_index(_source)`, `_method_mro_cache`,
`_self_receiver_class_name_cache`, `_never_gc_object_values`); their IR shapes
are a mix of own-index loads (`get_field 0/4`) and by-name dynamic attrs, i.e.
silently inconsistent state under pcc1.

## Proposals

- No.1 Complete the contract and ratchet it `[CONFIRMED]`

### Code Change

`L1_CODEGEN_HOST_ATTRS` gains all 13 attributes, so every host mixin read and
write resolves to one contract slot.  `tests/python/test_fallback_baseline.py::test_l1_codegen_host_contract_covers_every_mixin_self_state`
walks L1CodeGen's bases transitively (direct bases and the mixin stack) and
asserts every `self.<attr>` assignment target is a contract attribute; it
fails on the pre-fix contract with exactly the 13 names above.  The temporary
type/id probes are removed; the re-raise frame trail and the
`PCC_CODEGEN_EXCEPTION_CONTEXT` line before `str(exc)` in
`_codegen_trace_dump` are kept as the deliberate, env-gated diagnostic that
located this.

### [DENIED] treating the KeyError as a subscript-fix regression

The raising subscript only exposed the corruption; reverting it would restore
silent wrong state.  Recorded so the next reader does not "fix" the symptom.

### Status

`[CONFIRMED]` root cause and fix on the host side.  pcc1 confirmation: the
class_gen worker must compile with the raising dyn subscript in place and the
off/on full-cost A/B must run; see the evidence receipt.
