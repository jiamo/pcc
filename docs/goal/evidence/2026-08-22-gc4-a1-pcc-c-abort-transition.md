# GC4 A1 pcc-C abort transition boundary

Status: **GREEN for the narrow host-launched pcc-C, default/nonthread
`pcc_threads.c` translation-unit boundary only.**
`GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE` remains `IN_PROGRESS`.

## Supported claim

On Darwin arm64, the host-launched pcc C frontend can emit the current
`pcc_threads.c` translation unit through its normal runtime-source gate.  For
the explicit `PCC_WITH_THREADS=0` variant, the emitted object:

- has an undefined host-libc `abort` edge and no undefined
  `pcc_platform_abort` edge according to `nm -u`;
- links, with the pcc-emitted object placed before the host-C differential
  runtime archive, through host `cc` and `-lm`; and
- executes a probe that observes threads disabled and the no-park depth
  transition `0 -> 1 -> 0`.

This supersedes only the pcc-C transition-TU caveat in
`2026-08-22-gc4-a1-thread-quiescence-substrate.md`.  The mode is a
host/CPython-launched pcc C-frontend gate plus a host-C differential archive;
it is not pcc1, a strict pcc-Python runtime, or a self-hosted fixed point.  The
gate does not request or establish self-backend ownership.

## Frozen source and test identity

| Path | SHA-256 |
|---|---|
| `pcc/py_runtime/src/pcc_threads.c` | `5f5e4be2416a79d4ee04d6f61e63fb7cd24dce6a64468d65c48fd3dc7e29fa8a` |
| `pcc/py_runtime/include/py_runtime.h` | `f76b822f25a6600092e543144a7fa3095c85eec8feb70defcc71ec481b5ebef4` |
| `tests/python/test_py_runtime_pcc_emit.py` | `e12ed81c4bb49027fe8d5bc5bac8b7410cc15fbd373e7d3033670a60fc9ee266` |

These are dirty-worktree content identities, not a clean commit or release
manifest.

## Exact focused gate

```bash
gtimeout 390s zsh -o pipefail -c 'gtimeout 360s env -u LC_ALL -u PCC_RUNTIME_ARCHIVE -u PCC_WITH_LIBPYTHON -u PCC_REFCOUNT_KIND -u PCC_WITH_THREADS uv run pytest -vv -x -n0 --tb=short -m integration "tests/python/test_py_runtime_pcc_emit.py::test_pcc_emits_object_for_runtime_source[pcc_threads.c]" tests/python/test_py_runtime_pcc_emit.py::test_pcc_c_thread_transition_tu_links_host_libc_abort 2>&1 | tee build/gc4-a1-pcc-c-abort-transition-final.log'
```

Observed result: **2 passed in 1.75 seconds**, with a final pytest summary.
The durable log is
`build/gc4-a1-pcc-c-abort-transition-final.log`, SHA-256
`c86c05c0803fc98b68805e9714cf7e41a86a95964641623c2bc0b2dcbea6dc2d`.

The first node is the existing default runtime-source object-emission gate for
`pcc_threads.c`.  The second node independently emits the explicit nonthread
variant, inspects its undefined-symbol surface, links it against the host-C
dependency archive, and runs the depth probe.  A prior one-node execution of
the second node also passed in 2.46 seconds at log
`build/gc4-a1-pcc-c-abort-link.log`, SHA-256
`f5ea818ea4dbf8a34b0214f3baf7d77f706a55fb3503aa9bd2c4a947b9498d52`;
the two-node result above is the superseding final gate.

## Independent review

An independent read-only review of this exact narrow source/test boundary
reported **ZERO findings**.  The review did not widen the claim beyond the two
node IDs and mode labels above.

## Explicit nonclaims

This evidence does **not** prove:

- pcc-C emission, symbol ownership, link, or execution for
  `PCC_WITH_THREADS=1`;
- a complete runtime archive built by pcc-C, or complete archive provenance;
- the strict freestanding pcc-Python runtime or its
  `pcc_platform_abort` owner;
- any graph-lock, container raw-access, collector-phase, forwarded-payload, or
  physical Backend 4 relocation behavior;
- GC0..4 parity, a bootstrap stage, stage2 performance, pcc1/pcc2/pcc3, or a
  self-hosted fixed point.

No broad suite, bootstrap stage, GC4 relocation gate, or performance
measurement was run for this bounded transition proof.
