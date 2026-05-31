# Investigation: pcc1 stage2 times out inside `codegen[pcc.py_frontend.codegen.layer1]`

## Status

historical / superseded.

This document records an earlier stage2 timeout hypothesis. It is useful as a
timeline checkpoint, but it is no longer the active diagnosis. Later sampling
after GC/runtime and Layer1-walker fixes showed the process spending effectively
all CPU time in `user_py_set__lookup_slot`, caused by signed perturb probing in
the pcc-Python set runtime port. That issue is documented in:

- `docs/investigations/pcc-py-set-signed-perturb-bootstrap-timeout.md`

The current working tree now completes the default self-backend bootstrap path
under 20 seconds per stage:

- `pcc0 -> pcc1`: `18.33s`
- `pcc1 -> pcc2`: `18.88s`
- `pcc2 -> pcc3`: `18.85s`

Do not start by implementing the pending attribute-error trampoline proposals
below. Re-profile first if Layer1 codegen becomes slow again. The current
profile shows the prior 40s wall time was dominated by the host-text Python IR
pass pipeline. Layer1 nested free-name analysis now has memoization and a
profile regression test; it is no longer the best next assumption without fresh
evidence.

Background / earlier findings: see
`docs/investigations/pcc-bootstrap-stage2-type-infer-runtime-corruption.md`.
That file recorded several real fixes (Function.__init__ ownership,
Subscript borrowed semantics, list(list_obj) shallow copy, raw-scaffold
ownership conservatism) that changed the failure shape from
`Abort trap: 6` heap corruption in `type_infer[pcc.cli_bootstrap]`
(2026-05-05 era) to a compile-time blowup in
`codegen[pcc.py_frontend.codegen.layer1]` (2026-05-06 era).

This investigation only owns the new symptom: stage2 has not crashed,
it has stopped finishing within reasonable wall time, and the hot
path is repeated cold-error-handling IR generation around per-call
sites in the largest module (`layer1.py`, 28 301 lines).

## Problem Description

User report: pcc1 has been compiling pcc2 forever.

Concrete chain:

1. `pcc0 -> pcc1` (stage1): succeeds in roughly one minute.
2. `pcc1 -> pcc2` (stage2): does not finish within `timeout 600s`.
   The last lines of the verbose log show normal type_infer / codegen
   / IR-pass progress through the smaller modules and then enter
   `codegen[pcc.py_frontend.codegen.layer1]`, and stay there until
   the timeout fires (`exit=124`).

Stage2 is not aborting (`Abort trap: 6`) any longer; the active
blocker is wall-clock and memory growth inside Layer1 codegen, not
heap corruption.

## Repro

Single-binary repro using the latest stage1 build on this host.

```bash
PCC1=/tmp/pcc_bootstrap_attrerr.pTcvDN/pcc1   # 2026-05-06 17:02 build
test -x "$PCC1" || { echo "rebuild stage1 first" >&2; exit 1; }

# Sanity: trivial input still succeeds.
cat > /tmp/tiny.py <<'PY'
def main():
    return 0
PY
"$PCC1" --backend self --python-libpython off /tmp/tiny.py -o /tmp/tiny_o
echo "tiny exit=$?"   # expect 0

# The actual stage2 reproduction:
mkdir -p /tmp/pcc_repro
LOG=/tmp/stage2_repro.log
timeout 600s env PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py "$PCC1" \
  --verbose --backend self --python-libpython off \
  pcc/__main__.py -o /tmp/pcc_repro/pcc2 > "$LOG" 2>&1
echo "stage2 exit=$?"          # expect 124 (SIGTERM from timeout)
echo "--- last 5 lines ---"
tail -5 "$LOG"                 # expect: codegen[pcc.py_frontend.codegen.layer1]
```

Observed last lines (2026-05-06):

```
[pcc.py] type_infer[pcc.py_frontend.codegen.layer1]
[pcc.py] codegen pcc.py_frontend.codegen.layer1
[pcc.py] codegen[pcc.py_frontend.codegen.layer1]
```

Followed by 600 s of silence, exit 124.

A *narrower* repro that exercises the same hot path without compiling
the full 28k-line `layer1.py` is still TODO — see Test below.

## Test [CONFIRMED]

Confirmation criteria:

1. Same `$PCC1` finishes the trivial `tiny.py` repro with exit 0 in
   under 30 s. **Required** so we are sure the binary is functional.
2. Full stage2 invocation against `pcc/__main__.py` reaches the line
   `codegen[pcc.py_frontend.codegen.layer1]` and then stops
   producing log output until the 600 s timeout fires (exit 124).
3. Sampling the timed-out process shows the runtime call graph
   spending most of its time in the
   `_emit_attr -> _emit_attribute_error_if_null ->
    _emit_builtin_exception_and_branch -> _emit_exception_frame`
   chain, with `Constant.format` / `IRBuilder.call` / C string
   globals appearing as the leaves.

Confirmation observed in the prior investigation's
`Stage2 Sampling Result` section
(`/tmp/pcc_stage2_sample_layer1_after_cstring.txt`,
2.3 GB physical footprint, repeated `_emit_attribute_error_if_null`
on the stack). Re-running the repro on this branch produced
exit=124, last log line
`[pcc.py] codegen[pcc.py_frontend.codegen.layer1]`. Both halves of
the criteria match.

## Proposals

- No.1 Per-function shared attribute-error trampoline           [pending]
- No.2 Per-module C-string interner for traceback frame globals [pending]
- No.3 Lazy / on-demand error-frame emission                    [pending]
- No.4 IR-builder call constant memoization                     [pending]
- No.5 Cap layer1's IR with a hard "if module > 20k stmts, fall back to
       per-class chunked codegen"                                [pending]

## Workflow checkpoint — 2026-05-06

The investigation has progressed through **Steps 1-4** of the
`AGENTS.md > Investigation Workflow`:

* `## Repro` recorded.
* `## Test [CONFIRMED]` against the 17:02 stage1 build
  (`/tmp/pcc_bootstrap_attrerr.pTcvDN/pcc1`), exit 124, last log
  line `codegen[pcc.py_frontend.codegen.layer1]`, prior sampling
  result corroborates the call-graph hot path.
* Five proposals listed below, all `[pending]`.

The next step (Step 5: pick the first `[pending]` proposal, run
to verdict) was paused for two reasons:

1. There are 1 103 lines of uncommitted in-flight work on the
   working tree (codex's recent runtime-log / runtime-debug edits
   plus 294 lines of `layer1.py` changes). The 17:02 stage1
   binary already includes those, and rebuilding stage1 would
   silently re-bake whatever I write on top. Per the workflow's
   "no stacked unverified edits" rule, the first verdict cycle
   should start from a clean working tree.
2. Each proposal here (especially No.1) is a 1-2 hour structural
   change to the layer1 codegen path with five distinct edge
   cases (try/except err_target routing, async/await frames,
   span-line phi merging, debug-info SourceSpan threading,
   `--ir-scaffold=off` raw mode). Verdict per attempt costs
   ~5-15 min per stage2 retry. Running this loop responsibly
   needs a single dedicated session.

Specific resumption instructions for the next agent (Step 5):

1. Decide whether the in-flight working tree should be committed
   first. If it should, commit it and rerun the `## Repro` step
   on the post-commit binary; the `## Test [CONFIRMED]` claim
   above is for the 17:02 build only.
2. Pick the first `[pending]` proposal that the current evidence
   most directly supports. Today that is **No.1** — the sampling
   evidence (`_emit_attr -> _emit_attribute_error_if_null ->
   _emit_builtin_exception_and_branch -> _emit_exception_frame`)
   names exactly that code path; No.4 (constant memoization)
   would only help the leaf cost, which is not the dominant
   shape of the explosion.
3. Implement No.1 in `pcc/py_frontend/codegen/layer1.py` as a
   per-function shared error trampoline keyed by
   `(current_function, err_target)`; merge per-call-site span
   lines via a phi node on the trampoline's incoming edges.
   Preserve the per-call-site path for sites inside try blocks
   if the keying turns out non-trivial.
4. Rebuild stage1, rerun the `## Repro` recipe. Observe the new
   last-log-line / exit code. Mark No.1 `[CONFIRMED]` only if
   stage2 actually finishes (or the timed-out point shifts past
   `codegen[pcc.py_frontend.codegen.layer1]`); otherwise mark it
   `[DENIED]` with the new sampling evidence and move on to
   No.2 / No.3.
5. Commit `Code Change` + verdict text together. Do **not**
   bundle an unrelated runtime fix into the same commit.

## No.1 Per-function shared attribute-error trampoline

The hottest sample chain is:

```
_emit_attr
  _emit_attribute_error_if_null
    _emit_builtin_exception_and_branch
      _emit_exception_frame
```

Today every `o.attr` lowering re-emits the full "if attr is null then
build an AttributeError, push a frame, raise" block per call site.
Inside `layer1.py` itself there are thousands of `self.<thing>` /
`expr.<field>` accesses, so this multiplies.

Proposal: emit the AttributeError block **once per containing
function** as a single basic block. Each `_emit_attr` lowers to:

```
%v = call py_obj_getattr(...)
%isnull = call ptr_is_null(%v)
br i1 %isnull, label %attr_err.0, label %ok
```

`%attr_err.0` is the per-function shared trampoline that builds the
exception, pushes the frame, and raises. The traceback row carries
the call-site line number through a `phi` over the predecessors.

### Code Change

(pending — to be implemented in a follow-up commit on this
investigation file only.)

## No.2 Per-module C-string interner for traceback frame globals

The sampling showed `Constant_i64 / Constant__format` as a leaf cost
inside `_emit_exception_frame`. Each emitted frame stamps its file /
function / line metadata as fresh C-string globals.

Proposal: introduce a per-module string-pool keyed by
`(file_id, function_name, line)`. Subsequent calls return the
existing global instead of re-formatting + re-emitting. Acceptable
because traceback frame metadata is already required to be stable
per (file, line).

### Code Change

(pending.)

## No.3 Lazy / on-demand error-frame emission

The sampled hot path emits the error frame eagerly even for sites
that LLVM's later DCE will eliminate (e.g. attribute access on a
locally-known non-None value, or after a typed null check upstream
guarantees non-null).

Proposal: track per-emit-site whether the upstream type / null state
already proves the access cannot fail. When proven safe, skip
emitting the error frame entirely (no IR, no string globals). When
not provable, fall back to the shared trampoline from No.1.

### Code Change

(pending.)

## No.4 IR-builder call constant memoization

`Constant.format` round-trips small i32/i64 / string constants
through Python format paths. For repeated identical constants
(line numbers, hex digits, common opcodes) this is wasted work.

Proposal: per-builder cache keyed by `(type, value)` returning the
same `ir.Constant` instance. Already partially done for `_VOID` /
`_PYOBJ` types; extend to integer / small-string constants used by
the attribute-error chain.

### Code Change

(pending.)

## No.5 Per-class chunked codegen as a hard cap

If the per-emit fixes are not enough to bring layer1 stage2 codegen
under (say) 180 s, structural escape hatch: chunk codegen of large
modules by class. `layer1.py` is one giant `L1CodeGen` class with
461 methods; codegen could partition the methods into chunks and
emit them as separate LLVM functions referenced from the same
module, allowing parallel emission and bounded per-chunk memory.

Risk: cross-chunk inlining / private-helper visibility regression.

### Code Change

(pending.)
