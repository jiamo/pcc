# llvmlite API surface trace (β4.0)

Total unique callables hit: **142**

Total call events: **389225**

## Partition legend

- **A codegen-core**: >= 10 calls, hit from pcc.codegen / pcc.py_frontend.codegen

- **B passes-core**: >= 10 calls, hit from pcc.ir_passes / pcc.passes

- **C metadata/DWARF**: DIBuilder/DIFile/DIToken/Metadata APIs

- **D long-tail**: < 10 calls total

- **E binding**: llvmlite.binding (JIT, target, parse_assembly)


## A — codegen-core (β4.1 priority)

36 entries, 66707 calls total.

| API | Count | Hit from |
|---|---|---|
| `ir.Type.as_pointer` | 20414 | codegen:11, other:20396, py_frontend_codegen:7 |
| `ir.FunctionType.__init__` | 19333 | codegen:24, py_frontend_codegen:19309 |
| `ir.Function.__init__` | 19328 | codegen:19, py_frontend_codegen:19309 |
| `ir.Constant.__init__` | 1753 | codegen:110, other:60, py_frontend_codegen:1583 |
| `ir.IRBuilder.call` | 1088 | codegen:6, py_frontend_codegen:1082 |
| `ir.Function.append_basic_block` | 543 | codegen:39, py_frontend_codegen:504 |
| `ir.IRBuilder.load` | 481 | codegen:36, py_frontend_codegen:445 |
| `ir.IRBuilder.gep` | 413 | codegen:29, py_frontend_codegen:384 |
| `ir.IRBuilder.__init__` | 399 | codegen:51, py_frontend_codegen:348 |
| `ir.IRBuilder.store` | 371 | codegen:36, py_frontend_codegen:335 |
| `ir.IRBuilder.position_at_end` | 371 | codegen:51, py_frontend_codegen:320 |
| `ir.GlobalVariable.__init__` | 328 | codegen:2, py_frontend_codegen:326 |
| `ir.IRBuilder.alloca` | 307 | codegen:16, py_frontend_codegen:291 |
| `ir.ArrayType.__init__` | 291 | codegen:5, py_frontend_codegen:286 |
| `ir.Module.__init__` | 159 | codegen:17, py_frontend_codegen:142 |
| `ir.IRBuilder.ret` | 152 | codegen:21, py_frontend_codegen:131 |
| `ir.IRBuilder.ret_void` | 140 | py_frontend_codegen:140 |
| `ir.IRBuilder.position_before` | 117 | codegen:6, py_frontend_codegen:111 |
| `ir.IRBuilder.branch` | 116 | codegen:12, py_frontend_codegen:104 |
| `ir.IRBuilder.icmp_signed` | 92 | codegen:10, py_frontend_codegen:82 |
| `ir.IRBuilder.bitcast` | 85 | py_frontend_codegen:85 |
| `ir.IRBuilder.cbranch` | 71 | codegen:5, py_frontend_codegen:66 |
| `ir.IRBuilder.add` | 47 | codegen:10, py_frontend_codegen:37 |
| `ir.LiteralStructType.__init__` | 43 | py_frontend_codegen:43 |
| `ir.IRBuilder.sub` | 42 | codegen:4, other:26, py_frontend_codegen:12 |
| `ir.IRBuilder.unreachable` | 32 | py_frontend_codegen:32 |
| `ir.IRBuilder.select` | 31 | py_frontend_codegen:31 |
| `ir.FunctionAttributes.add` | 30 | codegen:18, py_frontend_codegen:12 |
| `ir.IRBuilder.neg` | 26 | codegen:1, py_frontend_codegen:25 |
| `ir.PhiInstr.add_incoming` | 20 | codegen:2, py_frontend_codegen:18 |
| `ir.IRBuilder.invoke` | 16 | py_frontend_codegen:16 |
| `ir.IRBuilder.landingpad` | 16 | py_frontend_codegen:16 |
| `ir.LandingPadInstr.add_clause` | 16 | py_frontend_codegen:16 |
| `ir.IRBuilder.extract_value` | 16 | py_frontend_codegen:16 |
| `ir.IRBuilder.phi` | 10 | codegen:1, py_frontend_codegen:9 |
| `ir.IRBuilder.zext` | 10 | codegen:5, py_frontend_codegen:5 |

## B — passes-core (β4.2 priority)

0 entries, 0 calls total.

| API | Count | Hit from |
|---|---|---|

## E — binding (β4.2 priority)

20 entries, 9768 calls total.

| API | Count | Hit from |
|---|---|---|
| `llvm.ValueRef.__init__` | 6379 | other:6379 |
| `llvm.parse_assembly` | 763 | evaluater:30, ir_passes:733 |
| `llvm.ContextRef.__init__` | 763 | other:763 |
| `llvm.ModuleRef.__init__` | 763 | other:763 |
| `llvm.ModuleRef.verify` | 733 | ir_passes:733 |
| `llvm.Target.create_target_machine` | 47 | codegen:17, evaluater:30 |
| `llvm.create_pipeline_tuning_options` | 30 | passes:30 |
| `llvm.PipelineTuningOptions.__init__` | 30 | other:30 |
| `llvm.create_pass_builder` | 30 | passes:30 |
| `llvm.PassBuilder.__init__` | 30 | other:30 |
| `llvm.PassBuilder.getModulePassManager` | 30 | passes:30 |
| `llvm.ModulePassManager.__init__` | 30 | other:30 |
| `llvm.NewPassManager.run` | 30 | passes:30 |
| `llvm.get_default_triple` | 18 | codegen:17, evaluater:1 |
| `llvm.initialize_native_target` | 17 | codegen:17 |
| `llvm.TargetMachine.emit_object` | 15 | evaluater:15 |
| `llvm.create_mcjit_compiler` | 15 | evaluater:15 |
| `llvm.ExecutionEngine.__init__` | 15 | other:15 |
| `llvm.ExecutionEngine.finalize_object` | 15 | evaluater:15 |
| `llvm.ExecutionEngine.get_function_address` | 15 | evaluater:15 |

## C — metadata / DWARF (β4.3 priority)

0 entries, 0 calls total.

| API | Count | Hit from |
|---|---|---|

## D — long-tail (β4.3 backlog)

86 entries, 312750 calls total.

| API | Count | Hit from |
|---|---|---|
| `ir.NamedValue.__init__` | 75664 | other:75664 |
| `ir.AttributeSet.__init__` | 73413 | other:73413 |
| `ir.ArgumentAttributes.__init__` | 51877 | other:51877 |
| `ir.PointerType.__init__` | 20434 | other:20434 |
| `ir.GlobalValue.__init__` | 19656 | other:19656 |
| `ir.Module.add_global` | 19656 | other:19656 |
| `ir.FunctionAttributes.__init__` | 19328 | other:19328 |
| `ir.Function.descr` | 8312 | other:8312 |
| `ir.Function.descr_prototype` | 8312 | other:8312 |
| `ir.Instruction.__init__` | 3588 | other:3588 |
| `ir.IntType.wrap_constant_value` | 1250 | other:1250 |
| `ir.IntType.format_constant` | 1206 | other:1206 |
| `ir.CallInstr.__init__` | 1104 | other:1104 |
| `ir.CallInstr.descr` | 1018 | other:1018 |
| `ir.Block.__init__` | 543 | other:543 |
| `ir.Block.descr` | 504 | other:504 |
| `ir.LoadInstr.__init__` | 481 | other:481 |
| `ir.Terminator.__init__` | 480 | other:480 |
| `ir.LoadInstr.descr` | 435 | other:435 |
| `ir.ArrayType.gep` | 419 | other:419 |
| `ir.GEPInstr.__init__` | 413 | other:413 |
| `ir.GEPInstr.descr` | 399 | other:399 |
| `ir.StoreInstr.__init__` | 371 | other:371 |
| `ir.StoreInstr.descr` | 337 | other:337 |
| `ir.AllocaInstr.__init__` | 307 | other:307 |
| `ir.GlobalVariable.descr` | 302 | other:302 |
| `ir.Aggregate.wrap_constant_value` | 297 | other:297 |
| `ir.Ret.__init__` | 292 | other:292 |
| `ir.Ret.descr` | 282 | other:282 |
| `ir.AllocaInstr.descr` | 268 | other:268 |
| `ir.Function.descr_body` | 261 | other:261 |
| `ir.Type.wrap_constant_value` | 206 | other:206 |
| `ir.Terminator.descr` | 179 | other:179 |
| `ir.Instruction.descr` | 124 | other:124 |
| `ir.CastInstr.__init__` | 108 | other:108 |
| `ir.CastInstr.descr` | 104 | other:104 |
| `ir.CompareInstr.__init__` | 92 | other:92 |
| `ir.CompareInstr.descr` | 87 | other:87 |
| `ir.Module.get_identified_types` | 75 | other:75 |
| `ir.BaseStructType.structure_repr` | 73 | other:73 |
| `ir.ArrayType.format_constant` | 44 | other:44 |
| `ir.Unreachable.__init__` | 32 | other:32 |
| `ir.IdentifiedStructType.get_declaration` | 32 | other:32 |
| `ir.SelectInstr.__init__` | 31 | other:31 |
| `ir.AttributeSet.add` | 30 | other:30 |
| `ir.SelectInstr.descr` | 29 | other:29 |
| `ir.Unreachable.descr` | 28 | other:28 |
| `ir.BaseStructType.format_constant` | 27 | other:27 |
| `ir.FormattedConstant.__init__` | 23 | other:23 |
| `ir.Type.format_constant` | 23 | other:23 |
| `ir.InvokeInstr.__init__` | 16 | other:16 |
| `ir.LandingPadInstr.__init__` | 16 | other:16 |
| `ir.ExtractValue.__init__` | 16 | other:16 |
| `ir.InvokeInstr.descr` | 14 | other:14 |
| `ir.LandingPadInstr.descr` | 14 | other:14 |
| `ir.ExtractValue.descr` | 14 | other:14 |
| `ir.BaseStructType.gep` | 14 | other:14 |
| `ir.PhiInstr.__init__` | 10 | other:10 |
| `ir.PhiInstr.descr` | 10 | other:10 |
| `ir.IRBuilder.mul` | 9 | codegen:3, py_frontend_codegen:6 |
| `ir.IRBuilder.xor` | 7 | other:1, py_frontend_codegen:6 |
| `ir.IRBuilder.and_` | 6 | py_frontend_codegen:6 |
| `ir.IRBuilder.srem` | 5 | py_frontend_codegen:5 |
| `ir.IRBuilder.sitofp` | 4 | py_frontend_codegen:4 |
| `ir.IRBuilder.sext` | 4 | codegen:4 |
| `ir.IRBuilder.or_` | 3 | codegen:2, py_frontend_codegen:1 |
| `ir.IRBuilder.sdiv` | 3 | py_frontend_codegen:3 |
| `ir.IRBuilder.trunc` | 3 | codegen:2, py_frontend_codegen:1 |
| `ir.Context.get_identified_type` | 3 | codegen:3 |
| `ir.IdentifiedStructType.__init__` | 3 | other:3 |
| `ir.IdentifiedStructType.set_body` | 3 | codegen:3 |
| `ir.IRBuilder.ptrtoint` | 2 | py_frontend_codegen:2 |
| `ir.SwitchInstr.add_case` | 2 | codegen:2 |
| `ir.IRBuilder.shl` | 1 | py_frontend_codegen:1 |
| `ir.IRBuilder.ashr` | 1 | py_frontend_codegen:1 |
| `ir.IRBuilder.fdiv` | 1 | py_frontend_codegen:1 |
| `ir.DoubleType.format_constant` | 1 | other:1 |
| `ir.IRBuilder.fadd` | 1 | py_frontend_codegen:1 |
| `ir.IRBuilder.fmul` | 1 | py_frontend_codegen:1 |
| `ir.IRBuilder.not_` | 1 | py_frontend_codegen:1 |
| ... (+6 more) | | |

## Samples (caller file:line for each top-A entry)

- `ir.Type.as_pointer` (20414 calls):
    - `pcc/codegen/c_codegen.py:20`
    - `pcc/codegen/c_codegen.py:21`
    - `pcc/codegen/c_codegen.py:217`
    - `pcc/codegen/c_codegen.py:220`
    - `pcc/codegen/c_codegen.py:221`
- `ir.FunctionType.__init__` (19333 calls):
    - `pcc/py_frontend/codegen/runtime_abi.py:236`
    - `pcc/py_frontend/codegen/layer1.py:380`
    - `pcc/py_frontend/codegen/layer1.py:587`
    - `pcc/py_frontend/codegen/layer1.py:335`
    - `pcc/py_frontend/codegen/layer1.py:1435`
- `ir.Function.__init__` (19328 calls):
    - `pcc/py_frontend/codegen/runtime_abi.py:237`
    - `pcc/py_frontend/codegen/layer1.py:381`
    - `pcc/py_frontend/codegen/layer1.py:593`
    - `pcc/py_frontend/codegen/layer1.py:336`
    - `pcc/py_frontend/codegen/layer1.py:1436`
- `ir.Constant.__init__` (1753 calls):
    - `.venv/lib/python3.13/site-packages/llvmlite/ir/types.py:87`
    - `pcc/py_frontend/codegen/layer1.py:1973`
    - `pcc/py_frontend/codegen/layer1.py:508`
    - `pcc/py_frontend/codegen/layer1.py:512`
    - `.venv/lib/python3.13/site-packages/llvmlite/ir/builder.py:555`
- `ir.IRBuilder.call` (1088 calls):
    - `pcc/py_frontend/codegen/layer1.py:2912`
    - `pcc/py_frontend/codegen/layer1.py:965`
    - `pcc/py_frontend/codegen/layer1.py:969`
    - `pcc/py_frontend/codegen/layer1.py:977`
    - `pcc/py_frontend/codegen/layer1.py:2111`
- `ir.Function.append_basic_block` (543 calls):
    - `pcc/py_frontend/codegen/layer1.py:604`
    - `pcc/py_frontend/codegen/layer1.py:337`
    - `pcc/py_frontend/codegen/layer1.py:2790`
    - `pcc/py_frontend/codegen/layer1.py:2791`
    - `pcc/py_frontend/codegen/layer1.py:1728`
- `ir.IRBuilder.load` (481 calls):
    - `pcc/py_frontend/codegen/layer1.py:2306`
    - `pcc/py_frontend/codegen/layer1.py:1899`
    - `pcc/py_frontend/codegen/layer1.py:1921`
    - `pcc/py_frontend/codegen/layer1.py:2257`
    - `pcc/py_frontend/codegen/class_gen.py:948`
- `ir.IRBuilder.gep` (413 calls):
    - `pcc/py_frontend/codegen/layer1.py:513`
    - `pcc/py_frontend/codegen/layer1.py:484`
    - `pcc/py_frontend/codegen/layer1.py:478`
    - `pcc/py_frontend/codegen/class_gen.py:714`
    - `pcc/py_frontend/codegen/class_gen.py:736`
- `ir.IRBuilder.__init__` (399 calls):
    - `pcc/py_frontend/codegen/layer1.py:605`
    - `pcc/py_frontend/codegen/layer1.py:343`
    - `pcc/py_frontend/codegen/layer1.py:900`
    - `pcc/py_frontend/codegen/class_gen.py:510`
    - `pcc/py_frontend/codegen/class_gen.py:596`
- `ir.IRBuilder.store` (371 calls):
    - `pcc/py_frontend/codegen/layer1.py:615`
    - `pcc/py_frontend/codegen/layer1.py:830`
    - `pcc/py_frontend/codegen/layer1.py:1886`
    - `pcc/py_frontend/codegen/layer1.py:1923`
    - `pcc/py_frontend/codegen/layer1.py:1676`
- `ir.IRBuilder.position_at_end` (371 calls):
    - `pcc/py_frontend/codegen/layer1.py:2805`
    - `pcc/py_frontend/codegen/layer1.py:2811`
    - `pcc/py_frontend/codegen/layer1.py:1734`
    - `pcc/py_frontend/codegen/layer1.py:1739`
    - `pcc/py_frontend/codegen/layer1.py:1747`
- `ir.GlobalVariable.__init__` (328 calls):
    - `pcc/py_frontend/codegen/layer1.py:505`
    - `pcc/py_frontend/codegen/layer1.py:449`
    - `pcc/py_frontend/codegen/class_gen.py:206`
    - `pcc/py_frontend/codegen/layer1.py:471`
    - `pcc/py_frontend/codegen/class_gen.py:707`
- `ir.IRBuilder.alloca` (307 calls):
    - `pcc/py_frontend/codegen/layer1.py:614`
    - `pcc/py_frontend/codegen/layer1.py:905`
    - `pcc/py_frontend/codegen/marshal.py:238`
    - `pcc/py_frontend/codegen/class_gen.py:531`
    - `pcc/py_frontend/codegen/class_gen.py:537`
- `ir.ArrayType.__init__` (291 calls):
    - `pcc/py_frontend/codegen/layer1.py:501`
    - `pcc/py_frontend/codegen/layer1.py:448`
    - `pcc/py_frontend/codegen/layer1.py:466`
    - `pcc/py_frontend/codegen/class_gen.py:702`
    - `pcc/py_frontend/codegen/class_gen.py:750`
- `ir.Module.__init__` (159 calls):
    - `pcc/py_frontend/codegen/layer1.py:145`
    - `pcc/py_frontend/codegen/layer1.py:196`
    - `pcc/codegen/c_codegen.py:753`
- `ir.IRBuilder.ret` (152 calls):
    - `pcc/py_frontend/codegen/layer1.py:735`
    - `pcc/py_frontend/codegen/layer1.py:362`
    - `pcc/py_frontend/codegen/layer1.py:630`
    - `pcc/codegen/c_codegen.py:9096`
    - `pcc/codegen/c_codegen.py:9365`
- `ir.IRBuilder.ret_void` (140 calls):
    - `pcc/py_frontend/codegen/layer1.py:628`
    - `pcc/py_frontend/codegen/class_gen.py:549`
    - `pcc/py_frontend/codegen/class_gen.py:604`
- `ir.IRBuilder.position_before` (117 calls):
    - `pcc/py_frontend/codegen/layer1.py:902`
    - `pcc/py_frontend/codegen/marshal.py:235`
    - `pcc/codegen/c_codegen.py:2821`
- `ir.IRBuilder.branch` (116 calls):
    - `pcc/py_frontend/codegen/layer1.py:2809`
    - `pcc/py_frontend/codegen/layer1.py:1737`
    - `pcc/py_frontend/codegen/layer1.py:1743`
    - `pcc/py_frontend/codegen/layer1.py:1759`
    - `pcc/py_frontend/codegen/layer1.py:1769`
- `ir.IRBuilder.icmp_signed` (92 calls):
    - `pcc/py_frontend/codegen/layer1.py:2456`
    - `pcc/py_frontend/codegen/layer1.py:2458`
    - `pcc/py_frontend/codegen/layer1.py:2460`
    - `pcc/py_frontend/codegen/layer1.py:2477`
    - `pcc/py_frontend/codegen/layer1.py:2479`