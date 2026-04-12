# llvmlite API surface trace (β4.0)

Total unique callables hit: **119**

Total call events: **385497**

## Partition legend

- **A codegen-core**: >= 10 calls, hit from pcc.codegen / pcc.py_frontend.codegen

- **B passes-core**: >= 10 calls, hit from pcc.ir_passes / pcc.passes

- **C metadata/DWARF**: DIBuilder/DIFile/DIToken/Metadata APIs

- **D long-tail**: < 10 calls total

- **E binding**: llvmlite.binding (JIT, target, parse_assembly)


## A — codegen-core (β4.1 priority)

34 entries, 66089 calls total.

| API | Count | Hit from |
|---|---|---|
| `ir.Type.as_pointer` | 20350 | codegen:10, other:20333, py_frontend_codegen:7 |
| `ir.FunctionType.__init__` | 19309 | py_frontend_codegen:19309 |
| `ir.Function.__init__` | 19309 | py_frontend_codegen:19309 |
| `ir.Constant.__init__` | 1636 | other:53, py_frontend_codegen:1583 |
| `ir.IRBuilder.call` | 1082 | py_frontend_codegen:1082 |
| `ir.Function.append_basic_block` | 504 | py_frontend_codegen:504 |
| `ir.IRBuilder.load` | 445 | py_frontend_codegen:445 |
| `ir.IRBuilder.gep` | 384 | py_frontend_codegen:384 |
| `ir.IRBuilder.__init__` | 350 | codegen:2, py_frontend_codegen:348 |
| `ir.IRBuilder.store` | 335 | py_frontend_codegen:335 |
| `ir.GlobalVariable.__init__` | 326 | py_frontend_codegen:326 |
| `ir.IRBuilder.position_at_end` | 320 | py_frontend_codegen:320 |
| `ir.IRBuilder.alloca` | 291 | py_frontend_codegen:291 |
| `ir.ArrayType.__init__` | 286 | py_frontend_codegen:286 |
| `ir.Module.__init__` | 144 | codegen:2, py_frontend_codegen:142 |
| `ir.IRBuilder.ret_void` | 140 | py_frontend_codegen:140 |
| `ir.IRBuilder.ret` | 131 | py_frontend_codegen:131 |
| `ir.IRBuilder.position_before` | 111 | py_frontend_codegen:111 |
| `ir.IRBuilder.branch` | 104 | py_frontend_codegen:104 |
| `ir.IRBuilder.bitcast` | 85 | py_frontend_codegen:85 |
| `ir.IRBuilder.icmp_signed` | 82 | py_frontend_codegen:82 |
| `ir.IRBuilder.cbranch` | 66 | py_frontend_codegen:66 |
| `ir.LiteralStructType.__init__` | 43 | py_frontend_codegen:43 |
| `ir.IRBuilder.add` | 37 | py_frontend_codegen:37 |
| `ir.IRBuilder.sub` | 37 | other:25, py_frontend_codegen:12 |
| `ir.IRBuilder.unreachable` | 32 | py_frontend_codegen:32 |
| `ir.IRBuilder.select` | 31 | py_frontend_codegen:31 |
| `ir.IRBuilder.neg` | 25 | py_frontend_codegen:25 |
| `ir.PhiInstr.add_incoming` | 18 | py_frontend_codegen:18 |
| `ir.IRBuilder.invoke` | 16 | py_frontend_codegen:16 |
| `ir.IRBuilder.landingpad` | 16 | py_frontend_codegen:16 |
| `ir.LandingPadInstr.add_clause` | 16 | py_frontend_codegen:16 |
| `ir.IRBuilder.extract_value` | 16 | py_frontend_codegen:16 |
| `ir.FunctionAttributes.add` | 12 | py_frontend_codegen:12 |

## B — passes-core (β4.2 priority)

0 entries, 0 calls total.

| API | Count | Hit from |
|---|---|---|

## E — binding (β4.2 priority)

5 entries, 8277 calls total.

| API | Count | Hit from |
|---|---|---|
| `llvm.ValueRef.__init__` | 5649 | other:5649 |
| `llvm.parse_assembly` | 657 | ir_passes:657 |
| `llvm.ContextRef.__init__` | 657 | other:657 |
| `llvm.ModuleRef.__init__` | 657 | other:657 |
| `llvm.ModuleRef.verify` | 657 | ir_passes:657 |

## C — metadata / DWARF (β4.3 priority)

0 entries, 0 calls total.

| API | Count | Hit from |
|---|---|---|

## D — long-tail (β4.3 backlog)

80 entries, 311131 calls total.

| API | Count | Hit from |
|---|---|---|
| `ir.NamedValue.__init__` | 75375 | other:75375 |
| `ir.AttributeSet.__init__` | 73357 | other:73357 |
| `ir.ArgumentAttributes.__init__` | 51852 | other:51852 |
| `ir.PointerType.__init__` | 20350 | other:20350 |
| `ir.GlobalValue.__init__` | 19635 | other:19635 |
| `ir.Module.add_global` | 19635 | other:19635 |
| `ir.FunctionAttributes.__init__` | 19309 | other:19309 |
| `ir.Function.descr` | 8293 | other:8293 |
| `ir.Function.descr_prototype` | 8293 | other:8293 |
| `ir.Instruction.__init__` | 3384 | other:3384 |
| `ir.IntType.wrap_constant_value` | 1140 | other:1140 |
| `ir.CallInstr.__init__` | 1098 | other:1098 |
| `ir.IntType.format_constant` | 1096 | other:1096 |
| `ir.CallInstr.descr` | 1012 | other:1012 |
| `ir.Block.__init__` | 504 | other:504 |
| `ir.Block.descr` | 465 | other:465 |
| `ir.LoadInstr.__init__` | 445 | other:445 |
| `ir.Terminator.__init__` | 441 | other:441 |
| `ir.ArrayType.gep` | 407 | other:407 |
| `ir.LoadInstr.descr` | 399 | other:399 |
| `ir.GEPInstr.__init__` | 384 | other:384 |
| `ir.GEPInstr.descr` | 370 | other:370 |
| `ir.StoreInstr.__init__` | 335 | other:335 |
| `ir.StoreInstr.descr` | 301 | other:301 |
| `ir.GlobalVariable.descr` | 300 | other:300 |
| `ir.Aggregate.wrap_constant_value` | 292 | other:292 |
| `ir.AllocaInstr.__init__` | 291 | other:291 |
| `ir.Ret.__init__` | 271 | other:271 |
| `ir.Ret.descr` | 261 | other:261 |
| `ir.AllocaInstr.descr` | 252 | other:252 |
| `ir.Function.descr_body` | 243 | other:243 |
| `ir.Type.wrap_constant_value` | 204 | other:204 |
| `ir.Terminator.descr` | 162 | other:162 |
| `ir.Instruction.descr` | 104 | other:104 |
| `ir.CastInstr.__init__` | 97 | other:97 |
| `ir.CastInstr.descr` | 93 | other:93 |
| `ir.CompareInstr.__init__` | 82 | other:82 |
| `ir.CompareInstr.descr` | 77 | other:77 |
| `ir.Module.get_identified_types` | 60 | other:60 |
| `ir.ArrayType.format_constant` | 43 | other:43 |
| `ir.BaseStructType.structure_repr` | 41 | other:41 |
| `ir.Unreachable.__init__` | 32 | other:32 |
| `ir.SelectInstr.__init__` | 31 | other:31 |
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
| `ir.AttributeSet.add` | 12 | other:12 |
| `ir.IRBuilder.phi` | 9 | py_frontend_codegen:9 |
| `ir.PhiInstr.__init__` | 9 | other:9 |
| `ir.PhiInstr.descr` | 9 | other:9 |
| `ir.IRBuilder.xor` | 7 | other:1, py_frontend_codegen:6 |
| `ir.IRBuilder.and_` | 6 | py_frontend_codegen:6 |
| `ir.IRBuilder.mul` | 6 | py_frontend_codegen:6 |
| `ir.IRBuilder.srem` | 5 | py_frontend_codegen:5 |
| `ir.IRBuilder.zext` | 5 | py_frontend_codegen:5 |
| `ir.IRBuilder.sitofp` | 4 | py_frontend_codegen:4 |
| `ir.IRBuilder.sdiv` | 3 | py_frontend_codegen:3 |
| `llvm.get_default_triple` | 3 | codegen:2, evaluater:1 |
| `ir.IRBuilder.ptrtoint` | 2 | py_frontend_codegen:2 |
| `llvm.initialize_native_target` | 2 | codegen:2 |
| `llvm.Target.create_target_machine` | 2 | codegen:2 |
| `ir.IRBuilder.or_` | 1 | py_frontend_codegen:1 |
| `ir.IRBuilder.shl` | 1 | py_frontend_codegen:1 |
| `ir.IRBuilder.ashr` | 1 | py_frontend_codegen:1 |
| `ir.IRBuilder.fdiv` | 1 | py_frontend_codegen:1 |
| `ir.DoubleType.format_constant` | 1 | other:1 |
| `ir.IRBuilder.fadd` | 1 | py_frontend_codegen:1 |
| `ir.IRBuilder.fmul` | 1 | py_frontend_codegen:1 |
| `ir.IRBuilder.trunc` | 1 | py_frontend_codegen:1 |
| `ir.IRBuilder.not_` | 1 | py_frontend_codegen:1 |
| `llvm.initialize_all_targets` | 1 | evaluater:1 |
| `llvm.initialize_all_asmprinters` | 1 | evaluater:1 |

## Samples (caller file:line for each top-A entry)

- `ir.Type.as_pointer` (20350 calls):
    - `pcc/codegen/c_codegen.py:20`
    - `pcc/codegen/c_codegen.py:21`
    - `pcc/codegen/c_codegen.py:217`
    - `pcc/codegen/c_codegen.py:220`
    - `pcc/codegen/c_codegen.py:221`
- `ir.FunctionType.__init__` (19309 calls):
    - `pcc/py_frontend/codegen/runtime_abi.py:236`
    - `pcc/py_frontend/codegen/layer1.py:380`
    - `pcc/py_frontend/codegen/layer1.py:587`
    - `pcc/py_frontend/codegen/layer1.py:335`
    - `pcc/py_frontend/codegen/layer1.py:1435`
- `ir.Function.__init__` (19309 calls):
    - `pcc/py_frontend/codegen/runtime_abi.py:237`
    - `pcc/py_frontend/codegen/layer1.py:381`
    - `pcc/py_frontend/codegen/layer1.py:593`
    - `pcc/py_frontend/codegen/layer1.py:336`
    - `pcc/py_frontend/codegen/layer1.py:1436`
- `ir.Constant.__init__` (1636 calls):
    - `.venv/lib/python3.13/site-packages/llvmlite/ir/types.py:87`
    - `pcc/py_frontend/codegen/layer1.py:1973`
    - `pcc/py_frontend/codegen/layer1.py:508`
    - `pcc/py_frontend/codegen/layer1.py:512`
    - `.venv/lib/python3.13/site-packages/llvmlite/ir/builder.py:555`
- `ir.IRBuilder.call` (1082 calls):
    - `pcc/py_frontend/codegen/layer1.py:2912`
    - `pcc/py_frontend/codegen/layer1.py:965`
    - `pcc/py_frontend/codegen/layer1.py:969`
    - `pcc/py_frontend/codegen/layer1.py:977`
    - `pcc/py_frontend/codegen/layer1.py:2111`
- `ir.Function.append_basic_block` (504 calls):
    - `pcc/py_frontend/codegen/layer1.py:604`
    - `pcc/py_frontend/codegen/layer1.py:337`
    - `pcc/py_frontend/codegen/layer1.py:2790`
    - `pcc/py_frontend/codegen/layer1.py:2791`
    - `pcc/py_frontend/codegen/layer1.py:1728`
- `ir.IRBuilder.load` (445 calls):
    - `pcc/py_frontend/codegen/layer1.py:2306`
    - `pcc/py_frontend/codegen/layer1.py:1899`
    - `pcc/py_frontend/codegen/layer1.py:1921`
    - `pcc/py_frontend/codegen/layer1.py:2257`
    - `pcc/py_frontend/codegen/class_gen.py:948`
- `ir.IRBuilder.gep` (384 calls):
    - `pcc/py_frontend/codegen/layer1.py:513`
    - `pcc/py_frontend/codegen/layer1.py:484`
    - `pcc/py_frontend/codegen/layer1.py:478`
    - `pcc/py_frontend/codegen/class_gen.py:714`
    - `pcc/py_frontend/codegen/class_gen.py:736`
- `ir.IRBuilder.__init__` (350 calls):
    - `pcc/py_frontend/codegen/layer1.py:605`
    - `pcc/py_frontend/codegen/layer1.py:343`
    - `pcc/py_frontend/codegen/layer1.py:900`
    - `pcc/py_frontend/codegen/class_gen.py:510`
    - `pcc/py_frontend/codegen/class_gen.py:596`
- `ir.IRBuilder.store` (335 calls):
    - `pcc/py_frontend/codegen/layer1.py:615`
    - `pcc/py_frontend/codegen/layer1.py:830`
    - `pcc/py_frontend/codegen/layer1.py:1886`
    - `pcc/py_frontend/codegen/layer1.py:1923`
    - `pcc/py_frontend/codegen/layer1.py:1676`
- `ir.GlobalVariable.__init__` (326 calls):
    - `pcc/py_frontend/codegen/layer1.py:505`
    - `pcc/py_frontend/codegen/layer1.py:449`
    - `pcc/py_frontend/codegen/class_gen.py:206`
    - `pcc/py_frontend/codegen/layer1.py:471`
    - `pcc/py_frontend/codegen/class_gen.py:707`
- `ir.IRBuilder.position_at_end` (320 calls):
    - `pcc/py_frontend/codegen/layer1.py:2805`
    - `pcc/py_frontend/codegen/layer1.py:2811`
    - `pcc/py_frontend/codegen/layer1.py:1734`
    - `pcc/py_frontend/codegen/layer1.py:1739`
    - `pcc/py_frontend/codegen/layer1.py:1747`
- `ir.IRBuilder.alloca` (291 calls):
    - `pcc/py_frontend/codegen/layer1.py:614`
    - `pcc/py_frontend/codegen/layer1.py:905`
    - `pcc/py_frontend/codegen/marshal.py:238`
    - `pcc/py_frontend/codegen/class_gen.py:531`
    - `pcc/py_frontend/codegen/class_gen.py:537`
- `ir.ArrayType.__init__` (286 calls):
    - `pcc/py_frontend/codegen/layer1.py:501`
    - `pcc/py_frontend/codegen/layer1.py:448`
    - `pcc/py_frontend/codegen/layer1.py:466`
    - `pcc/py_frontend/codegen/class_gen.py:702`
    - `pcc/py_frontend/codegen/class_gen.py:750`
- `ir.Module.__init__` (144 calls):
    - `pcc/py_frontend/codegen/layer1.py:145`
    - `pcc/py_frontend/codegen/layer1.py:196`
    - `pcc/codegen/c_codegen.py:753`
- `ir.IRBuilder.ret_void` (140 calls):
    - `pcc/py_frontend/codegen/layer1.py:628`
    - `pcc/py_frontend/codegen/class_gen.py:549`
    - `pcc/py_frontend/codegen/class_gen.py:604`
- `ir.IRBuilder.ret` (131 calls):
    - `pcc/py_frontend/codegen/layer1.py:735`
    - `pcc/py_frontend/codegen/layer1.py:362`
    - `pcc/py_frontend/codegen/layer1.py:630`
- `ir.IRBuilder.position_before` (111 calls):
    - `pcc/py_frontend/codegen/layer1.py:902`
    - `pcc/py_frontend/codegen/marshal.py:235`
- `ir.IRBuilder.branch` (104 calls):
    - `pcc/py_frontend/codegen/layer1.py:2809`
    - `pcc/py_frontend/codegen/layer1.py:1737`
    - `pcc/py_frontend/codegen/layer1.py:1743`
    - `pcc/py_frontend/codegen/layer1.py:1759`
    - `pcc/py_frontend/codegen/layer1.py:1769`
- `ir.IRBuilder.bitcast` (85 calls):
    - `pcc/py_frontend/codegen/class_gen.py:667`
    - `pcc/py_frontend/codegen/class_gen.py:684`
    - `pcc/py_frontend/codegen/class_gen.py:663`