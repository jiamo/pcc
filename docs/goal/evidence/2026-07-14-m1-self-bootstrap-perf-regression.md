# M1 self-bootstrap performance-regression evidence

Date: 2026-07-14

Task: `M1-BOOTSTRAP-PERF-REGRESSION`

## Result

The current strict `backend=self`, `python-libpython=off`, GC0 bootstrap
again completes in a finite envelope. The formal gate reused one fresh shared
pcc1, then completed the full pcc1 -> pcc2 -> pcc3 chain in 287.69 seconds.
Its checked stage profiles total 285.866 seconds after shared stage1, below the
600-second task boundary. pcc2 and pcc3 are byte-identical after the existing
Mach-O code-signature / LC_UUID normalization.

No closure module was excluded, no fallback boundary was widened, and no GC
semantics were disabled.

## Closure and named-IR attribution

The 2026-06-02 checked GC0 reference profile records 112 modules and
83,872,536 IR bytes. The current formal GC0 stage2 profile records 146 modules
and 179,766,851 IR bytes.

Comparing `__pcc_py_module_fini_*` symbols in the retained 2026-06-02 pcc2
and current pcc2 gives exactly 112 -> 146: 34 additions and zero removals. The
34 additions are the first-class `pcc.backend.self_backend*` closure plus
`pcc.cli_bootstrap_array_core`, `pcc.package_schema`, and
`pcc.py_frontend.codegen.builtin_exceptions`. They are semantically reachable
compiler/backend modules, so deleting them to recover the old timing would
violate the self-backend execution-root contract.

A current pcc1 emit-only run produced 146 named IR sections:

```text
file bytes:                    179,775,958
sum of module IR bytes:        179,766,851
named modules:                 146
new 34 module IR bytes:         26,976,531
retained 112 module IR bytes:  152,790,320
SHA-256: b503fa40338e5abdfabaaeb80cc9d1a6b6188fadf4aedc1226733fa7e48011ad
```

Thus the byte growth has two explicit components: 26,976,531 bytes from the
34 required new modules, and aggregate growth in the retained 112-module
compiler/frontend set from the old 83,872,536-byte whole closure to a current
152,790,320 bytes. The current named table below makes the latter concentrated
surfaces inspectable; the largest are `pipeline`, `cli_bootstrap`,
`class_gen`, `type_infer`, and the large call/method lowering modules.
The optimization retains both components instead of hiding them.

## Before/after profiles

| Profile | Modules | IR bytes | Native emitter | Stage compiler total |
|---|---:|---:|---:|---:|
| 2026-06-02 GC0 cache-hit reference stage2 | 112 | 83,872,536 | 1.711 s | 17.029 s |
| pre-fix current GC1 stage2 | 146 | 179,568,660 | 1,055.915 s | 1,182.849 s |
| post-fix formal current GC0 stage2 | 146 | 179,766,851 | 69.721 s | 142.286 s |
| post-fix formal current GC0 stage3 | 146 | 179,766,851 | 69.765 s | 142.278 s |

The controlled same-input A/B used a retained 4,686,078-byte
`pcc.py_frontend.type_infer` IR probe:

```text
old pcc1 worker: 31.00 s
new pcc1 worker:  7.83 s
speedup:          3.96x
object SHA-256 (both):
dc1ae954eba88c065ce0d8e0a713d30b7a0088983e043f1ea1e8dbc00d66e45d
```

Source-hosted stage1 also fell from observed 597-700+ second runs to 57.742
seconds after source-worker emission and generic self-backend hot-path fixes.

## Root causes and fixes

1. Large compiled-stage modules were split in the parent process, retaining
   huge live IR and preventing pcc1 from isolating splitter memory. A hidden
   compiled-stage splitter worker now writes a validated shard manifest; nine
   modules become 43 shards in the formal profile.
2. The splitter's global-reference renamer used per-character string
   concatenation. Slice accumulation plus one final join changed a 14.4 MB
   pcc1 split from 66 seconds / about 22 GB RSS to 1.38 seconds / about
   283 MB RSS.
3. Self-backend materialization performed the false-hash compatibility linear
   slot scan before recognizing globals and literals. Known locals now take
   the normal O(1) mapping path; constants/globals are classified before the
   compatibility fallback. Stack-slot used-value membership also uses stable
   hash buckets rather than a full list scan.
4. Native `re.Pattern.match/search` rebuilt `ReProg` on every call, and
   Match construction compiled again for group names. The standalone C engine
   now uses a bounded 64-entry, append-only, thread-safe compiled-program
   cache shared by the C and pcc-Python runtime tiers. Cache-full/allocation
   failure preserves the original uncached behavior.

A rolling scheduler experiment with the same 180 tasks took 280.62 seconds
versus the existing scheduler's 280.18-second pre-cache profile, so scheduler
batch barriers were denied as the cause.

## Formal gates

```text
gtimeout 900s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py
1 passed in 287.69s
stage2 wall: 142.936s
stage3 wall: 142.930s
normalized pcc2/pcc3: identical
```

```text
tests/python/test_re_engine_differential.py
289 passed in 2.45s

strict self/no-libpython native Pattern object
1 passed in 32.49s

tests/c/test_self_backend.py
277 passed

fallback ratchet
20 passed in 225.26s

IR fallback ratchet
3 passed in 1.00s

bootstrap shim regression file
86 passed in 307.08s

second synthetic PEP489 strict pcc1 package regression
1 passed in 8.35s
```

No full GCC validation was run.

## Current per-module IR bytes

| Module | IR bytes | Added since 112-module reference |
|---|---:|:---:|
| `pcc.__main__` | 197500 | no |
| `pcc.backend` | 229762 | yes |
| `pcc.backend.self_backend_aarch64_darwin` | 1595048 | yes |
| `pcc.backend.self_backend_aarch64_darwin_abi` | 252148 | yes |
| `pcc.backend.self_backend_aarch64_darwin_addr` | 253788 | yes |
| `pcc.backend.self_backend_aarch64_darwin_branch_protection` | 78692 | yes |
| `pcc.backend.self_backend_aarch64_darwin_calls` | 3861070 | yes |
| `pcc.backend.self_backend_aarch64_darwin_compute` | 1995726 | yes |
| `pcc.backend.self_backend_aarch64_darwin_data` | 482571 | yes |
| `pcc.backend.self_backend_aarch64_darwin_flow` | 398854 | yes |
| `pcc.backend.self_backend_aarch64_darwin_materialize` | 1420677 | yes |
| `pcc.backend.self_backend_aarch64_darwin_mem` | 142133 | yes |
| `pcc.backend.self_backend_aarch64_darwin_memory` | 259140 | yes |
| `pcc.backend.self_backend_aarch64_darwin_ops` | 1018210 | yes |
| `pcc.backend.self_backend_aarch64_darwin_prologue` | 105601 | yes |
| `pcc.backend.self_backend_aarch64_darwin_regs` | 317461 | yes |
| `pcc.backend.self_backend_aarch64_darwin_returns` | 92550 | yes |
| `pcc.backend.self_backend_aarch64_darwin_slots` | 846682 | yes |
| `pcc.backend.self_backend_aarch64_darwin_symbols` | 100744 | yes |
| `pcc.backend.self_backend_aarch64_darwin_terminators` | 185664 | yes |
| `pcc.backend.self_backend_analysis` | 316004 | yes |
| `pcc.backend.self_backend_emit` | 74311 | yes |
| `pcc.backend.self_backend_float_bits` | 85815 | yes |
| `pcc.backend.self_backend_instruction_dispatch` | 89232 | yes |
| `pcc.backend.self_backend_ir` | 780324 | yes |
| `pcc.backend.self_backend_module_symbols` | 126311 | yes |
| `pcc.backend.self_backend_parse` | 4455579 | yes |
| `pcc.backend.self_backend_prepare` | 134574 | yes |
| `pcc.backend.self_backend_stackprep` | 389372 | yes |
| `pcc.backend.self_backend_target_match` | 54724 | yes |
| `pcc.backend.self_backend_target_passes` | 361840 | yes |
| `pcc.backend.self_backend_terminator_dispatch` | 132164 | yes |
| `pcc.cli_bootstrap` | 9095242 | no |
| `pcc.cli_bootstrap_array_core` | 6106408 | yes |
| `pcc.llvm_capi.ir` | 3822027 | no |
| `pcc.package_schema` | 164703 | yes |
| `pcc.parse.py_lex` | 645987 | no |
| `pcc.parse.py_lift` | 1925777 | no |
| `pcc.parse.py_parse` | 4898434 | no |
| `pcc.py_frontend` | 43139 | no |
| `pcc.py_frontend.codegen._l1_codegen_static_methods` | 4045718 | no |
| `pcc.py_frontend.codegen.assignment_statement_lowering` | 2135374 | no |
| `pcc.py_frontend.codegen.assignment_store_lowering` | 599318 | no |
| `pcc.py_frontend.codegen.async_with_lowering` | 775205 | no |
| `pcc.py_frontend.codegen.attr_load_lowering` | 2490626 | no |
| `pcc.py_frontend.codegen.attr_store_lowering` | 611755 | no |
| `pcc.py_frontend.codegen.binary_op_lowering` | 1154343 | no |
| `pcc.py_frontend.codegen.builtin_exceptions` | 68649 | yes |
| `pcc.py_frontend.codegen.builtin_type_attr_lowering` | 714908 | no |
| `pcc.py_frontend.codegen.call_arg_lowering` | 491547 | no |
| `pcc.py_frontend.codegen.call_expression_lowering` | 4320525 | no |
| `pcc.py_frontend.codegen.call_object_lowering` | 537605 | no |
| `pcc.py_frontend.codegen.call_resolution_lowering` | 1008594 | no |
| `pcc.py_frontend.codegen.class_alias_lowering` | 404590 | no |
| `pcc.py_frontend.codegen.class_gen` | 7368442 | no |
| `pcc.py_frontend.codegen.class_model_lowering` | 1226793 | no |
| `pcc.py_frontend.codegen.coercion_lowering` | 685506 | no |
| `pcc.py_frontend.codegen.compare_membership_lowering` | 1434858 | no |
| `pcc.py_frontend.codegen.comprehension_lowering` | 1383580 | no |
| `pcc.py_frontend.codegen.control_flow_lowering` | 772140 | no |
| `pcc.py_frontend.codegen.core_helpers` | 521621 | no |
| `pcc.py_frontend.codegen.cpy_bridge_lowering` | 532359 | no |
| `pcc.py_frontend.codegen.cpy_call_lowering` | 895432 | no |
| `pcc.py_frontend.codegen.cpy_import_state` | 478267 | no |
| `pcc.py_frontend.codegen.cpy_return_analysis` | 495933 | no |
| `pcc.py_frontend.codegen.decorator_lowering` | 491289 | no |
| `pcc.py_frontend.codegen.delete_lowering` | 564117 | no |
| `pcc.py_frontend.codegen.dict_lowering` | 769432 | no |
| `pcc.py_frontend.codegen.dynamic_type_lowering` | 536940 | no |
| `pcc.py_frontend.codegen.errors` | 45217 | no |
| `pcc.py_frontend.codegen.exact_int_lowering` | 651060 | no |
| `pcc.py_frontend.codegen.exception_lowering` | 1276097 | no |
| `pcc.py_frontend.codegen.expr_dispatch_lowering` | 946974 | no |
| `pcc.py_frontend.codegen.expr_helper_lowering` | 682190 | no |
| `pcc.py_frontend.codegen.extern_func_info_lowering` | 484298 | no |
| `pcc.py_frontend.codegen.extern_lowering` | 569087 | no |
| `pcc.py_frontend.codegen.for_loop_lowering` | 1987960 | no |
| `pcc.py_frontend.codegen.for_normalization_lowering` | 705271 | no |
| `pcc.py_frontend.codegen.format_lowering` | 1194876 | no |
| `pcc.py_frontend.codegen.generation_lowering` | 1309135 | no |
| `pcc.py_frontend.codegen.generator_lowering` | 1128198 | no |
| `pcc.py_frontend.codegen.hoist_analysis` | 1520672 | no |
| `pcc.py_frontend.codegen.hoist_lowering` | 3975770 | no |
| `pcc.py_frontend.codegen.host_contract` | 286408 | no |
| `pcc.py_frontend.codegen.import_lowering` | 1468201 | no |
| `pcc.py_frontend.codegen.ir_decl_helpers` | 429061 | no |
| `pcc.py_frontend.codegen.ir_scaffold_lowering` | 2155080 | no |
| `pcc.py_frontend.codegen.isinstance_lowering` | 910221 | no |
| `pcc.py_frontend.codegen.iterator_builtin_lowering` | 595695 | no |
| `pcc.py_frontend.codegen.lambda_callback_lowering` | 556787 | no |
| `pcc.py_frontend.codegen.lambda_helpers_lowering` | 1389143 | no |
| `pcc.py_frontend.codegen.layer1` | 288995 | no |
| `pcc.py_frontend.codegen.layer1_constants` | 73978 | no |
| `pcc.py_frontend.codegen.layer1_entrypoints` | 631517 | no |
| `pcc.py_frontend.codegen.layer1_init` | 530341 | no |
| `pcc.py_frontend.codegen.layer1_mixins` | 387770 | no |
| `pcc.py_frontend.codegen.layer1_support` | 2096214 | no |
| `pcc.py_frontend.codegen.list_builtin_lowering` | 742234 | no |
| `pcc.py_frontend.codegen.list_method_lowering` | 1417476 | no |
| `pcc.py_frontend.codegen.literal_lowering` | 1133479 | no |
| `pcc.py_frontend.codegen.marshal` | 443578 | no |
| `pcc.py_frontend.codegen.method_call_expression_lowering` | 3504727 | no |
| `pcc.py_frontend.codegen.method_call_lowering` | 1094209 | no |
| `pcc.py_frontend.codegen.module_global_lowering` | 1072046 | no |
| `pcc.py_frontend.codegen.module_lifecycle_lowering` | 635696 | no |
| `pcc.py_frontend.codegen.module_name_lowering` | 385557 | no |
| `pcc.py_frontend.codegen.name_lowering` | 1064011 | no |
| `pcc.py_frontend.codegen.native_asyncio` | 488710 | no |
| `pcc.py_frontend.codegen.native_dataclasses` | 457314 | no |
| `pcc.py_frontend.codegen.native_files` | 693856 | no |
| `pcc.py_frontend.codegen.native_gc` | 547175 | no |
| `pcc.py_frontend.codegen.native_math` | 690490 | no |
| `pcc.py_frontend.codegen.native_modules` | 3540153 | no |
| `pcc.py_frontend.codegen.native_os` | 928763 | no |
| `pcc.py_frontend.codegen.native_system` | 834947 | no |
| `pcc.py_frontend.codegen.native_text_modules` | 1634766 | no |
| `pcc.py_frontend.codegen.native_threading` | 909681 | no |
| `pcc.py_frontend.codegen.native_virtual_thread` | 824373 | no |
| `pcc.py_frontend.codegen.native_weakref` | 499683 | no |
| `pcc.py_frontend.codegen.numeric_builtin_lowering` | 1771027 | no |
| `pcc.py_frontend.codegen.ownership_lowering` | 1407500 | no |
| `pcc.py_frontend.codegen.print_lowering` | 584985 | no |
| `pcc.py_frontend.codegen.return_lowering` | 642143 | no |
| `pcc.py_frontend.codegen.runtime_abi` | 2554276 | no |
| `pcc.py_frontend.codegen.set_lowering` | 674236 | no |
| `pcc.py_frontend.codegen.static_test_runner_lowering` | 823416 | no |
| `pcc.py_frontend.codegen.stmt_dispatch_lowering` | 650750 | no |
| `pcc.py_frontend.codegen.stmt_misc_lowering` | 690126 | no |
| `pcc.py_frontend.codegen.string_globals_lowering` | 585462 | no |
| `pcc.py_frontend.codegen.string_method_lowering` | 1337448 | no |
| `pcc.py_frontend.codegen.subscript_lowering` | 996149 | no |
| `pcc.py_frontend.codegen.tuple_zip_lowering` | 668393 | no |
| `pcc.py_frontend.codegen.type_abi_lowering` | 1148301 | no |
| `pcc.py_frontend.codegen.typed_int_abi` | 1096120 | no |
| `pcc.py_frontend.codegen.typing_lowering` | 542417 | no |
| `pcc.py_frontend.codegen.unary_call_lowering` | 765853 | no |
| `pcc.py_frontend.codegen.unsafe_lowering` | 1354602 | no |
| `pcc.py_frontend.codegen.user_function_decl_lowering` | 506696 | no |
| `pcc.py_frontend.codegen.user_function_lowering` | 3000061 | no |
| `pcc.py_frontend.export_meta` | 288803 | no |
| `pcc.py_frontend.low_ir` | 420425 | no |
| `pcc.py_frontend.pipeline` | 11111803 | no |
| `pcc.py_frontend.py_ast` | 2372379 | no |
| `pcc.py_frontend.py_ast_contract` | 177267 | no |
| `pcc.py_frontend.type_infer` | 6149178 | no |
| `pcc.py_frontend.types` | 576441 | no |
