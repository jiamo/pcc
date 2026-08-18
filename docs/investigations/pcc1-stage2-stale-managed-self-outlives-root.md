# Investigation: with reloads restored, pcc1 rejects its own IR — `self` outlives its active root in 19 functions

## Status

resolved — root cause fixed in the runtime; the Stage2 rejection is gone and pcc1 now matches host pcc byte-for-byte on the reproducer

Predecessor:
[`set-and-frozenset-of-dict-lower-to-empty.md`](set-and-frozenset-of-dict-lower-to-empty.md)
— that fix is what made this reachable. Stage2 storyline:
[`pcc1-stage2-emit-throughput-and-memory.md`](pcc1-stage2-emit-throughput-and-memory.md).

## Problem Description

Restoring `frozenset(mapping)` populated `managed_names` in
`build_function_stack_map_plan`, which turned on a validation in
`_planned_managed_reloads` that had **never executed** while that set was
empty:

```python
if origin.root_offset not in active_offsets:
    _fail(func, f"stale managed SSA value {name!r} outlives its active root")
```

On the first Stage2 built from the fixed source it fires 19 times:

```text
18 x  stale managed SSA value 'self'
 1 x  stale managed SSA value 'stop'
```

in `format_lowering`, `subscript_lowering`, `native_os`,
`class_model_lowering`, `type_abi_lowering`, `class_gen`, `native_modules`,
`generator_lowering`, `ownership_lowering`, `self_backend_ir`, and others.
18 of 19 are the **method receiver `self`**, which makes this systematic
rather than a property of one function.

The claim is *not* yet that the compiler is wrong. Either:

- **(a)** the frontend genuinely lets a managed value outlive the root that
  keeps it reachable, in which case the missing reloads were hiding a real
  GC-soundness hole on the moving backends, and this validation is doing its
  job; or
- **(b)** the liveness feeding it over-approximates — `_managed_live_after` is
  SSA liveness, while `active_offsets` is bounded by the frame protocol
  (`_apply_frame_protocol` mutates `active` at `pcc_gc_frame_enter` /
  `pcc_gc_frame_leave`), so a value that is dead on every taken path after a
  frame leave could still be reported live.

Both are plausible and they need different fixes. Do not pick one from the
message text.

## The discriminating fact

**Stage1 passed.** Host pcc compiled the same 212 modules from the same source
with the same fix and the same validation, in 271.721 s, `rc=0`. So host pcc
does not hit this on *its* IR, and pcc1 does hit it on *pcc1's* IR.

The two differ in both inputs to the analysis: Stage2's IR is produced by
pcc1's frontend, and the analysis itself is pcc1's compiled copy. That makes
this a second host-versus-pcc1 divergence, in the same family as the one the
predecessor closed — and the same technique should localize it: emit one
failing module's IR with host pcc and with pcc1 and diff, before touching
either the analysis or the frontend.

## Repro

```bash
D=$PWD/build/bootstrap-setdict-family-v1
env -u LC_ALL PCC_BOOTSTRAP_OUT_DIR="$D" PCC_BOOTSTRAP_PROFILE_DIR="$D/profile" \
  PCC_SELF_BACKEND_OBJECT_CACHE_DIR="$D/cache-cold" PCC_GC_BACKEND=0 \
  scripts/bootstrap.sh --backend self --stage 3 --reuse-stage1
# PCC_BOOTSTRAP_STAGE_FAILED stage=2 elapsed_ms=655403 rc=1
grep -c 'stale managed SSA value' "$D/stage23.log"   # 19
```

pcc1 used: `ff257a16a6b194b47a16404c401d98f6050edf39ab7d526b9d41d00dff67a4f1`,
built by host pcc from the fixed source (Stage1 `elapsed_ms=271721`, `rc=0`).

## Test [CONFIRMED]

Observed under the command above. Not yet reduced to a small reproducer — the
next step is a single failing module, not a whole stage.

## Proposals

- No.1 diff one failing module's IR, host pcc versus pcc1, before changing anything `[CONFIRMED]`
- No.2 localize the wrong `_root_group` locations under pcc1 `[CONFIRMED]`
- No.3 instrument `_root_group` itself: which of its three empty-locations paths fires `[pending]`

## No.1 diff one failing module's IR, host versus pcc1

### Code Change

None. This is a localization step, deliberately ahead of any fix.

Take one failing module — `pcc/py_frontend/codegen/format_lowering.py` is the
first in the log — emit its self-backend IR with host pcc and with pcc1, and
diff the frame-protocol and root traffic around `self`. If the IR is identical,
the divergence is in the compiled analysis and (b) is likely; if the IR differs
in where `self`'s root is entered or left, the divergence is in the frontend and
(a) is in play.

Explicitly rejected shortcuts, so a later session does not reach for them:

- **Do not narrow `managed_names` to make the stage pass.** That re-creates the
  exact bug the predecessor fixed, and it is the failure disposition already
  recorded on the task row.
- **Do not downgrade `_fail` to a warning or a skip.** The check is either
  correct — in which case silencing it restores a GC hole — or wrong, in which
  case it should be *fixed*, not muted.
- **Do not revert the mapping fix.** It is proven correct, byte-identical to
  host on a frozen item, and independently regression-tested.

## No.1 result — localized to the analysis, not the frontend `[CONFIRMED]`

### A 15-second reproducer replaces the 655-second stage

The frozen per-item IR from an earlier Stage2 reproduces it exactly. Same
file, two compilers, both built from the current fixed source:

```bash
export PCC_GC_BACKEND=0 PCC_PYTHON_IR_PASSES=off PYTHONHASHSEED=0
IR=build/stage2-current-object-inputs-no62-v1/item_343.ll   # format_lowering
<pcc1> --pcc-self-backend-emit-worker "$IR" out.result out.s ""
```

```text
pcc1 (fixed)  REJECTS: stale managed SSA value 'self.4818.22' outlives its active root
host pcc      ACCEPTS: emits assembly ae5db2c61681352a…
```

The IR is byte-identical for both, so **the frontend is exonerated and the
divergence is inside the compiled analysis**. Hypothesis (a) — "the frontend
lets a managed value outlive its root" — is therefore `[DENIED]` as the cause
of *this* rejection.

### The diagnostic was unactionable, so it was fixed first

`_planned_managed_reloads` reported only the value name. It now reports the
root it wanted, the roots that were live, and the live set — all of which were
already in hand at that line. This is a permanent improvement, not a probe.

### The exact disagreement

With the enriched message, on the same IR and the same function:

```text
host:  root_offset=-32  active_offsets=[-152, -144, -56, -40, -32]
pcc1:  root_offset=-32  active_offsets=[-152, -144, -88, -72, -64, -56, -40]
both:  live_values=['self.4818.22']
```

Both agree on `root_offset = -32` and on the live set, so
`_managed_value_origins` and `_managed_live_after` agree. They disagree only on
`active_offsets`:

```text
common      -152, -144, -56, -40
host only   -32
pcc1 only   -88, -72, -64
```

That is not "one root was left too early" — pcc1 has *more* offsets, and they
are different ones. It is a **group whose `locations` are computed wrongly**,
which points at `_root_group` and what feeds it: `_frame_map_count` (reads the
frame-map global's first i32, whose sign carries `owned`),
`_resolve_pointer`, and `parsed_function_alloca_slot` /`alloca.offset` in

```python
frame_offset = -alloca.offset + origin.offset + index * POINTER_SIZE
```

Next probe goes there, and only there.

### Hypotheses killed by probe before this, recorded so they are not retried

Each was plausible, each took about a minute, each is wrong:

```text
nested set comprehension over dict.values()   {l.offset for g in d.values() for l in g.locations}   CORRECT
the new live_after list-of-lists shape        [EMPTY for _ in instrs] + per_block[i] = frozenset()  CORRECT
the nonlocal version-memo closure             cached_offsets_version / cached_active_offsets        CORRECT
```

Three guesses, three denials. The instrumented run that produced the numbers
above took one rebuild and one 15-second run, and it is what actually
localized the bug — the same lesson this repository has already written down
twice.

## No.2 result — narrowed to ONE group with empty locations `[CONFIRMED]`

The reload planner knew the value and the offsets but not where in the CFG it
was, so that context was added at the call site (free there, and a permanent
improvement rather than a probe). The failure now reads:

```text
stale managed SSA value 'self.4818.22' outlives its active root:
  wants root_offset=-32,
  active_offsets=[-152, -144, -88, -72, -64, -56, -40],
  live_values=['self.4818.22']
  [at block #2 'call.cont.4837' instr #0,
   active_groups=['body.addr.7@0', 'call.ret.root.4832.29@0',
                  'for.obj.iter.root.4846.43@0', 'found.addr.18@0',
                  'rebind_count.addr.13@0', 'self.addr.4@0',
                  'stmt.for.obj.addr.47@0', 'target_name.addr.10@0']]
```

**Eight groups, seven offsets, and `self.addr.4@0` is present.** So `active` is
correct and `_apply_frame_protocol` is correct: the root was never removed. The
group is there and contributes **no location**. Hypothesis (b) — over-approximate
liveness against a frame-protocol-bounded active set — is therefore `[DENIED]`
too. Both original hypotheses are now dead.

`_root_group` returns `_RootGroup(key, ())` on exactly three paths:

```python
count, owned = _frame_map_count(...)          # 1. count == 0
if count == 0 or origin.base == "null":       # 2. base == "null"
    return _RootGroup(key, ())
alloca = parsed_function_alloca_slot(func, origin.base)
if alloca is None:
    if origin.base.startswith("@") or any(arg.name == origin.base for arg in func.args):
        return _RootGroup(key, ())            # 3. alloca missing + escape
```

Host's per-group dump for the same function (obtained by monkeypatching
`_root_group` in-process — no rebuild needed) shows every group with exactly
one location:

```text
self.addr.4@0               map=@.pcc.gc.frame.map.borrowed.1  count=1 owned=False  [-32]
body.addr.7@0               map=@.pcc.gc.frame.map.borrowed.1  count=1 owned=False  [-144]
target_name.addr.10@0       map=@.pcc.gc.frame.map.borrowed.1  count=1 owned=False  [-152]
found.addr.18@0             map=@.pcc.gc.frame.map.borrowed.1  count=1 owned=False  [-56]
rebind_count.addr.13@0      map=@.pcc.gc.frame.map.1           count=1 owned=True   [-40]
call.ret.root.4832.29@0     map=@.pcc.gc.frame.lifo.map…       count=1 owned=True   [-64]
for.obj.iter.root.4846.43@0 map=@.pcc.gc.frame.map.1           count=1 owned=True   [-72]
stmt.for.obj.addr.47@0      map=@.pcc.gc.frame.map.1           count=1 owned=True   [-88]
```

The facts that make this narrow:

* `@.pcc.gc.frame.map.borrowed.1 = internal constant i32 -1`, so
  `count = abs(-1) = 1` and `owned = (-1 > 0) = False`.
* **Three other groups read that same global and work fine under pcc1**
  (`body` -144, `target_name` -152, `found` -56 are all present). So a blanket
  failure to parse the negative initializer is ruled out.
* `%self.addr.4 = alloca ptr` really is an alloca, and the function's
  parameters are `%.1, %.2, %.3` — so the `any(arg.name == origin.base …)`
  escape should be False for `"self.addr.4"`.

So under pcc1, for this one group, one of those three paths is taken and its
siblings' identical inputs are not. Next instrumentation goes inside
`_root_group` itself and prints, per group, `count`, whether
`parsed_function_alloca_slot` found the alloca, and which escape fired.

### Hypotheses killed by probe, recorded so they are not retried

Seven, each ~1 minute, each wrong:

```text
nested set comprehension over dict.values()      CORRECT
the new live_after list-of-lists shape           CORRECT
the nonlocal version-memo closure                CORRECT
sorted() over the exact root-key strings         CORRECT
any() over a generator expression                CORRECT
BFS queue pop(0) + [None] * n                    CORRECT
the _dot_numeric_text_key_id / names_equal family CORRECT
```

Every one of these was a reasonable guess from reading the code, and every one
cost a minute to disprove. The two steps that actually moved this forward were
both *instrumentation of the real path*: enriching the diagnostic, then adding
CFG context to it. That is the third time this file's family has taught the
same lesson.

## Notes on measurement hygiene

The Stage2 run that produced this is a **diagnostic run, not a baseline**:

- it failed, so it has no wall time to compare against anything;
- it was launched without an outer `gtimeout`, which violates the repository's
  hard timeout rule. No stray children survived (`ps` clean after exit), but
  the next chain must be launched under a watchdog.

## No.3 result — ROOT CAUSE: `set` silently drops elements whose probe sequence cycles `[CONFIRMED]`

### The group was not empty

Adding an empty-group diagnosis at the failure site returned
`groups_without_locations=[]`. All eight groups have locations. So the set
comprehension that builds `active_offsets` received 8 offsets and produced 7:
**the set itself lost one.**

### Reproduced in 30 lines, then in 8

```text
set of the exact eight offsets      pcc-native n=7, -32 missing   CPython n=8
plain s.add() of the same eight     pcc-native n=7, -32 missing   CPython n=8
```

so it is not the comprehension. Sweeping by size and sign:

```text
negative multiples of 8   n=1..5 correct;  n=6 len=5 missing -48
                          n=7 len=5 missing -48,-56
                          n=8 len=5 missing -48,-56,-64
positive multiples of 8   correct at every n
-1, -2, -3 …              correct at every n
```

`n=6` is exactly where a capacity-8 set grows: `_maybe_grow` uses
`threshold = (capacity * 2) // 3 = 5` and grows when `fill > threshold`.

### The mechanism, in pure Python

`_set_add`'s probe loop is CPython's perturb recurrence and nothing else:

```python
perturb = hash_val
j = hash_val & mask
while probes < capacity * 2:
    ...
    perturb = _perturb_shift5(perturb)
    j = (j * 5 + perturb + 1) & mask
```

Simulating that faithfully at capacity 8 with the negative keys:

```text
inserting -48 after -8,-16,-24,-32,-40
  probe slots visited: [0, 7, 3, 7, 3, 7, 3, 7, 3, 7, 3, 7, 3, 0, 1, 6]
  distinct slots reached: {0, 1, 3, 6, 7}
  table:  [-8, -16, None, -40, None, None, -24, -32]
```

Slots **2, 4 and 5 are free and are never visited**. The sequence oscillates
between 7 and 3 until the `capacity * 2` budget runs out, and the element is
then **dropped with no error**. The same keys with positive sign do not cycle
and all six insert cleanly.

Two defects, and the second is the dangerous one:

1. **The probe sequence is not guaranteed to cover the table.** CPython's set
   is not this recurrence alone — it probes `j, j+1, … j+LINEAR_PROBES`
   linearly *before* perturbing, and that linear window is what guarantees
   coverage on small tables. pcc's port omits it.
2. **Exhausting the probe budget is silent.** The add returns as if it
   succeeded. A set that quietly forgets an element is the worst possible
   failure mode, and it is what produced a GC stack-map that omitted a live
   root.

### Not port drift — the C mirror has it too

```text
default (pcc-Python ports)   n=6 len=5, -48 missing
PCC_RUNTIME_CC=cc (C source) n=6 len=5, -48 missing
```

So this is an original defect shared by both implementations, not a
port-versus-C divergence. It is invisible to host pcc because host pcc runs on
CPython's own `set`.

### Why this closes the Stage2 chain

`active_offsets` is a set of frame offsets. Frame offsets are negative and
pointer-aligned, so they are **exactly the shape that triggers this** —
negative multiples of 8, all colliding into the same starting slot. Eight live
roots is enough. `self.addr.4`'s offset -32 was dropped from that set, the
reload planner then found its root "not active", and 19 functions were
rejected. Every layer above was behaving correctly.

### Deliberately not fixed in this slice

The fix touches `py_set.py` and `py_set.c` together, and the same
`while probes < capacity * 2` shape appears in `_dict_rooted_op`, so the dict
needs the same audit before a fix is designed. Candidate directions, none yet
chosen: restore CPython's linear-probe window; make the recurrence
full-period; or at minimum make budget exhaustion fail loudly instead of
dropping. That is its own task with its own regression, gated on the five-GC
matrix because it changes a core container for every backend.

## Fix landed — probe budget corrected in both containers and both mirrors

The defect was the **probe budget**, not the recurrence. `perturb` needs
**13** shifts to decay from a 64-bit value to zero, and only once it is zero is
`j = (j * 5 + 1) & mask` a full-period generator over the table (a=5, c=1,
m=2**k satisfies Hull-Dobell). The bound `capacity * 2` gave three
full-period probes at capacity 8, so the loop could give up while free slots
existed.

This corrects a claim written in this file's own task row before the mechanism
was understood: "a larger budget still cycles" is **wrong**. A budget of
`capacity + 16` — 13 decay steps plus a full period, with margin — is provably
sufficient at every capacity, and is *tighter* than `capacity * 2` for large
tables. What the earlier note got right is that silent dropping is the real
hazard; that is now covered by a regression rather than by hope.

Ten probe loops changed, growth thresholds deliberately untouched:

```text
pcc/py_runtime/py/py_set.py    3   (add/lookup, the mode loop, _rehash_find_empty_slot)
pcc/py_runtime/py/py_dict.py   2
pcc/py_runtime/src/py_set.c    3
pcc/py_runtime/src/py_dict.c   2
```

### Verification

```text
set  of negative pointer-aligned keys   pcc == CPython at every n in 1..12
dict of negative pointer-aligned keys   pcc == CPython at every n in 1..12
the original 8-offset reproducer        n_offsets=8, has -32
C mirror (PCC_RUNTIME_CC=cc)            identical to CPython
```

`tests/python/test_native_set_dict_probe_coverage.py` — **3 failed before the
fix, 3 passed after**, with the runtime archive rebuilt on both sides. It
covers set and dict across the capacity-8 and capacity-16 growth boundaries,
and pins the `13 + capacity` bound as a stated proof obligation plus a source
check that no probe loop returns to `capacity * 2`.

### The blocker is closed

Stage1 rebuilt on the fixed runtime (`elapsed_ms=107820`, `rc=0`), then the
15-second reproducer:

```text
pcc1 (fixed) on item_343   ae5db2c61681352a…   rc=0, no rejection
host pcc     on item_343   ae5db2c61681352a…   byte-identical
```

The rejection is gone *and* the two compilers now agree byte-for-byte on this
module, which is the stronger result. Focused gate across the
set/dict/stack-map/mapping families: **102 passed**.

A cold Stage2 + Stage3 is running under a watchdog on this runtime; its result
belongs in the Stage2 storyline, not here.
