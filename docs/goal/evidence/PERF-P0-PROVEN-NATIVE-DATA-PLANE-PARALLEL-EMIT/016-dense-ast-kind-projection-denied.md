# No.97 dense AST kind projection `[DENIED]`

## Claim

Replace repeated closed-world Expr/Stmt class dispatch with one dense integer
kind stored in a compiler-private physical slot, while preserving Python
dataclass semantics and the stable AST wire. The pre-registered retention line
was exact output plus three alternating frontend-worker pairs with median wall
and CPU speedup at least 1.08x, improving instructions, and footprint no worse
than 1.05x.

## Correctness work discovered by the slice

The first implementation exposed three distinct boundaries before timing:

1. a `dataclasses.field(...)` value cannot be serialized as a cross-module
   default, so `kind_id` must be physical-only rather than a semantic
   constructor field;
2. pcc's context exporter incorrectly treated unannotated dataclass class
   constants as instance fields;
3. generated derived dataclass initializers failed to call an inherited
   `__post_init__`.

It also exposed why the existing py_ast no-init constructor fast path cannot
bind against physical field order: `(span, kind_id, expr)` mapped the semantic
second argument into `kind_id`. The repaired fast path binds against the
exported semantic initializer fields, preserves arbitrary-precision literal
materialization, and writes the dense slot separately. A rejected intermediate
path lowered the FNV offset basis through `py_int_from_i64(0)`; structural IR
diff caught it, and the final candidate restored the exact output oracle.

Focused evidence:

- 99/99 combined dataclass, cross-module ABI, AST/wire, type-infer, dispatch,
  no-init class, self-compile, lift-stack and strict no-libpython nodes passed;
- No.89 and candidate host worker output:
  26,635,233 bytes, exact `23c7fa96...`;
- item311 assembly: exact `ff943e10...`;
- candidate pcc1: `1538dafd...`, Mach-O arm64, libSystem-only;
- candidate native worker: exact 19,279,474-byte `065100ba...`;
- supported current pcc1 link mode (CPython 3.15 host subprocess for the
  repository-owned Mach-O driver) compiled and ran `print(1)`; setting
  `PCC_HOST_PYTHON=/usr/bin/false` fails identically on No.89 and candidate and
  is therefore the pre-existing link-driver ownership boundary, not a No.97
  regression.

## Three alternating pairs

Both arms received one balanced warmup. All measured runs used the same frozen
AST/native-exports input, GC0, threads off, no host Python, timing disabled,
the shared performance lock, `/usr/bin/time -lp`, process-tree RSS sampling,
and byte-identical output.

| pair/order | wall B/C | CPU B/C | instructions C/B | footprint C/B | tree RSS C/B |
|---|---:|---:|---:|---:|---:|
| 1 B/C | 0.98264 | 0.98259 | 1.00950 | 0.99951 | 1.05030 |
| 2 C/B | 0.98785 | 0.98782 | 1.00910 | 0.99953 | 1.03759 |
| 3 B/C | 0.98919 | 0.98789 | 1.00976 | 0.99950 | 1.01807 |
| median | **0.98785** | **0.98782** | **1.00950** | **0.99951** | **1.03759** |

## Verdict

`[DENIED]`. The candidate is about 1.2% slower in wall/CPU, retires about 0.95%
more instructions, and does not improve footprint. It misses the 1.08x line
and moves in the wrong direction. No Stage2, Stage3, or GC1--4 gate is
authorized from this result.

The dense-kind production path is forward-removed. The independently valid
generic correctness repairs for inherited dataclass post-init and unannotated
dataclass class constants remain with their focused regressions. The frozen
candidate binary is retained only for a postmortem caller profile before the
next proposal.

Artifacts:

- `build/no97-dense-ast-stage1-candidate-315-v6/`
- `build/no97-frontend-ab-warmup-{baseline,candidate}-v1/`
- `build/no97-frontend-ab-pair{1,2,3}-{baseline,candidate}-v1/`
- `build/no97-exact9-final-focused-gate.log`

