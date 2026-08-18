# 037 — source Sections encode directly to validated packed PCO

Date: 2026-09-04

## Current owner

The source-frozen v20 full-lifecycle `py_ast` profile (24,564 samples) showed
that the remaining PCO tail was not primarily the instruction parser:

```text
NativeObject validation             19.00% inclusive
NativeObject.from_sections          17.53%
assemble_lines                      15.41%
encode_native_object                 4.91%
```

The old worker validated the source `Section` graph, materialized a second
`NativeSymbol`/`NativeSection`/`NativeRelocation` graph, validated that graph
by converting it back to Sections, then repeated the native validation and
source conversion before encoding. This is the compiler-internal object
projection named by the native-data-plane task.

## Change

`encode_native_object_from_sections` now:

1. runs the complete existing source `Section` validator;
2. emits canonical symbol/section/relocation records directly into final byte
   chunks without constructing the intermediate native dataclass graph; and
3. runs the complete, fail-closed `decode_packed_native_object` validator over
   the exact immutable bytes that cross the cache/process boundary.

This is not the denied No.70 trusted-worker shortcut: no provenance flag skips
validation, and disk/wire bytes are revalidated. The deleted work is only the
second semantic representation and repeated round trips. The legacy
materializing API remains the oracle and public API.

Canonical symbol partition/order is differential-tested, including an
intentionally unordered source-symbol input. A regression replaces all three
native record constructors with tripwires and proves the direct codec does not
call them. Another tripwire proves final packed validation remains mandatory.

## Expensive failures closed before retry

The first source-frozen pcc1 built successfully but its function smoke exposed
the first execution of packed decoding under pcc1:

```text
LookupError: pcc-native bytes decode supports utf-8 only
```

Both materializing and packed readers used `.decode("ascii")`. The complete
two-site family now rejects any byte >=128 explicitly, then uses the owned
UTF-8 decoder; ASCII is a strict UTF-8 subset, so the original non-ASCII
diagnostic and wire contract remain exact. A source ratchet forbids the old
codec spelling.

The second transfer reached final packed validation and exposed its retained
special-section tuple projection:

```text
NativeObjectError: every stack-map function address needs exactly one relocation
```

The same source Section validator had already accepted the exact relocation
set. The packed special-section path no longer materializes
`tuple(generator-of-relocation-fields)`: mod-init, compact-unwind and stackmap
all cursor-walk fixed relocation records. Stackmap keeps only
`offset -> record_index` and re-reads the selected record for validation. All
original shape, count, relocation and zero-address checks remain. The next
source-frozen pcc1 and its function smoke passed.

## Gates

```text
direct codec + native object focused packet             18 passed
native object + precise stackmap after pcc1 fixes       53 passed
full codec/link/exec/incremental/stackmap/direct packet 136 passed
native_object.py strict no-libpython closure            PASS
worker strict no-libpython closure                      PASS
```

The direct codec and worker functions are real generated functions with no
`strict.nolib.stub` or `py_cpy` edge in their bodies.

Source-frozen successful transfer:

```text
source SHA-256            5b5f466f40a5f018c6ee7d3d7854422b0fc41f1e2a034dc8310938f3599a147e
pcc1 SHA-256              a0dfdc04c62d2c1050b4f863c92d641abe6630eaa83487fbc9e5bc31506a4f26
Stage1 wall / tree CPU    166.15s / 670.72s
process-tree peak         4,995,317,760 bytes (8 GiB hard cap)
linkage / canary          libSystem only / function canary green
```

The Stage1 value is transfer evidence, not a paired speed verdict.

## Current-pcc1 worker verdict

Both arms use `PCC_PY_FRONTEND_WORKER_TIMING=1` and the exact same frozen v14
`worker_156.manifest`, AST, export wire and GC0/no-libpython/direct-indexed
environment.

```text
metric                 v20 materialized control   v23 direct codec   reduction
wall                   35.53s                     28.76s             19.05%
user+sys CPU           35.47s                     28.63s             19.28%
instructions           548.264076B                389.072609B        29.04%
max RSS                4,085,792,768 B            3,977,166,848 B    2.66%
peak footprint         4,040,871,224 B            3,932,245,256 B    2.69%
worker codegen         34.644s                    27.783s            19.80%
direct indexed emit     6.868s                     7.211s             -4.99%
```

The direct emit subphase did not create the win; deleting the native-object
projection removed about 6.9 seconds from the post-emit tail. Both arms
publish byte-identical PCO SHA-256
`9987edea18e5fde14fd279cfdbfdfefc429eb9bddbae99122102702f4f13d3c0`.

## Verdict

`[CONFIRMED]` and retained. This is a material pcc1 instruction/CPU/wall win
with complete validation and no output change. It does not complete the
structured instruction plane or Stage2<=Stage1 claim. The v23 worker still
spends about 20.6 seconds outside direct indexed emit; re-profile v23 before
selecting the next structured family. No Stage2 ran.
