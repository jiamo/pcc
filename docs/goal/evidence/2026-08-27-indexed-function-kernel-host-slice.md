# Indexed Function Kernel host/self-closure slice — 2026-08-27

Claim level: focused compiler-architecture evidence only. This does **not**
yet prove the item311 pcc1 performance threshold, Stage2 transfer, fixed point,
or five-GC equality.

Implemented one shared `IndexedFunctionKernel` across verifier, stack
preparation, precise stack-map planning/liveness, AArch64 register allocation,
target planning, and AArch64 instruction emission. The supported path reads
opcode/data/def-use spans through stable block/value/type/opcode IDs. Legacy
instruction object projection is explicit and counted; stack preparation no
longer publishes string-keyed used-value or last-use compatibility tables.

Focused evidence:

- `gtimeout 240s env -u LC_ALL uv run pytest -q -x -n0 tests/c/test_self_backend*.py tests/python/test_precise_stackmap_abi.py`
  — 512 passed.
- Frozen item311 host emit: 59,984 instructions, zero
  `CompactParsedInstrView` constructions on the normal path, assembly SHA-256
  `ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`.
- Strict `--backend self --python-libpython=off --ir-scaffold=on
  --python-library --emit-llvm` closure passed for the new kernel and every
  changed compiler module except the already-existing standalone
  `self_backend_aarch64_darwin.py` list-splat limitation. Each of the 12
  generated closure IR modules was then actually emitted by the host self
  backend; all succeeded.
- The first frozen pcc0 -> pcc1 attempt exposed a real verifier failure in the
  new diagnostic-only `legacy_used_values` comprehension (`i64` passed to a
  pointer index helper). The code was reduced to an explicit integer loop;
  normal stackprep projection was removed; the new kernel and materializer
  closure IR now both pass full self-backend emission. The Stage1 attempt has
  not yet been rerun because an unrelated long GC3 bootstrap owns the machine.

Open boundary: build the source-frozen pcc1 candidate after the performance
machine is idle, run the item311 worker gate against a single-variable control,
and require at least 1.25x wall/instruction improvement plus lower memory and
exact assembly before any Stage2/Stage3 or five-GC transfer.

## pcc1 worker result

Source-frozen candidate `21c615ff...` / compiler `1df30556...` produced the
same item311 assembly as fresh control `dd808447...`, but missed the registered
25% worker gate:

| metric | control | indexed candidate | control/candidate |
|---|---:|---:|---:|
| wall | 44.60 s | 41.32 s | 1.079x |
| CPU | 44.05 s | 41.03 s | 1.074x |
| instructions | 636.39 B | 599.88 B | 1.061x |
| footprint | 6.874 GB | 4.999 GB | 1.375x lower |

The result proves a material memory win and exact output, but it does not
accept the slice or authorize Stage2. A follow-up scalar raw-i64 arena
substitution was also denied: 43.88 s, 607.97 B instructions, and 4.981 GB.
It added 1.35% instructions over the indexed-list candidate while saving only
0.36% footprint. Fresh early/late caller profiles bound arena scalar-access
overhead near 1%, while stack-map planning remains about 35% and AArch64
function/call/materialization emission remains the other absolute owner.

The scalar arena was therefore removed from the kernel hot path. The next
value-model slice must operate on whole packed records/spans (safepoint,
location, reload, slot/type/operand records) per call; another object method per
scalar is not a native projection.
