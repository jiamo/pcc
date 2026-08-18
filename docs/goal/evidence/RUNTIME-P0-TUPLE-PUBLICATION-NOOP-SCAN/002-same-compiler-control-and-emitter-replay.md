# Tuple completion scan: same-compiler control, linear scaling, emitter replay

Date: 2026-09-06. Status: native scaling and same-input emitter improvement
proven on GC0 with exact outputs; staged Stage2/Stage3 qualification not run.

## Source identity

HEAD `2203dc3d` is byte-identical to the frozen v80 snapshot
(`build/preload-identity-stage1-v80/source-snapshot`) in every build input
except `pcc/py_runtime/src/py_tuple.c` and `pcc/py_runtime/py/py_tuple.py`
(the GC0..3 early return before the completion scan). Support files
(`AGENTS.md`, `pyproject.toml`, the four link scripts) and
`utils/fake_libc_include` are identical. A read-only `git archive HEAD`
snapshot was frozen at
`/private/tmp/claude-501/-Users-jiamo-my-pcc/b71a4c0b-df4d-4cf3-a57a-66da611b2975/scratchpad/pcc-tuple-noop-scan-v81`.

## Same-compiler control runtime

`tests.runtime_build_cache.cached_pcc_python_runtime(runtime_source=<v80 py_runtime>)`
built the pre-fix tuple sources with the current compiler under the
performance lock and 8 GiB guard (28.2 s, peak 679,067,648 B):

```text
control   ea82adb9aa3a29624dcd4a08-pcc-py  archive 8fe5c67b19c08cd8...
candidate c8c9dc4b0c27003a4f3737f8-pcc-py  archive 658c2c956af1c49b...
```

`build/tuple-runtime-control-vs-candidate-member-comparison.json`: one
codegen checksum `5a9a2c83...` for both, `changed_sources == ["py_tuple.o"]`,
128 changed objects. `strings` shows exactly 128 members embed their own
staging path (`.../<key>-pcc-py.<tmp>/py_runtime/py/*.py`), so the object
drift is path embedding, not compiler drift. The old frozen v80 bundle has a
different codegen checksum and is therefore not the control.

## Native N/2N scaling (exact CPython output at every size)

`/usr/bin/time -lp` instructions retired for `tuple([1] * N)`
(`tests/python/test_tuple_publication_scaling.py` binaries, GC0):

| N | control (pre-fix, same compiler) | candidate |
| ---: | ---: | ---: |
| 10,000 | 1,224,152,962 | 23,619,777 |
| 20,000 | 4,824,874,353 | 22,750,078 |
| 40,000 | 19,238,083,913 | 29,981,553 |
| 1,000,000 | not run | 397,660,460 |
| 2,000,000 | not run | 784,971,142 |
| 4,000,000 | not run | 1,544,177,432 |

Control grows 3.94x then 3.99x per doubling; candidate grows 1.974x then
1.967x per doubling once startup stops dominating. RSS 19.2/35.1/67.1 MB at
1M/2M/4M. Artifacts: `build/tuple-growth-{control,candidate}-n*/`.

## Candidate Stage1 (v81)

`scripts/run_pcc_stage1_build.py --arm candidate` from the frozen snapshot
with the candidate archive, v80 recipe (frontend 7, self-backend 2, link 8,
direct indexed emit, GC0, 8 GiB guard):

```text
                 v80 control        v81 candidate
wall             185.70 s           187.21 s
user+sys         736.87 s           730.65 s
guard tree peak  5,048,434,688 B    4,827,348,992 B
pcc1 sha256      682830c2...        5fd934f0...
linkage          libSystem only     libSystem only   (function smoke 42)
```

Receipts: `build/tuple-noop-scan-stage1-v81/`,
`build/tuple-noop-scan-v81-build-guard/`. Stage1 is a build receipt, not a
speed claim.

## Receipt-bound same-input emitter replay

Each worker replays one retained v80 Stage2 sidecar through
`pcc1 --pcc-self-backend-indexed-emit-worker` under the recorded Stage2
environment (`os.execve` launcher, private HOME/TMPDIR, own runtime bundle),
inside `run_process_tree_sample.py` with the lock and 8 GiB guard.

`py_ast` (`module_83.direct.pidx`, 14,911,552 B, PCO lane), two alternating
pairs, PCO byte-identical to the retained `cb81f6c22c9b43ee...` in all four:

| run | wall | user | instructions | max RSS |
| --- | ---: | ---: | ---: | ---: |
| control-1 | 15.51 s | 15.05 s | 229,591,148,154 | 1,052,753,920 B |
| candidate-1 | 12.39 s | 12.00 s | 172,015,555,782 | 1,052,770,304 B |
| control-2 | 15.57 s | 15.13 s | 229,527,258,032 | 1,052,753,920 B |
| candidate-2 | 12.47 s | 12.04 s | 171,978,382,835 | 1,052,786,688 B |

Worker CPU 1.25x, instructions -25.1%, RSS flat, output exact.

`cli_bootstrap` (`module_1.direct.pidx`, 60,659,005 B, ASM lane), one pair,
ASM byte-identical to `04b55bb2c3faa64f...`: control 27.54 s / 26.62 s user /
397,800,036,084 instructions; candidate 27.21 s / 26.28 s / 395,094,794,254
(-0.7%). The tuple scan is not the owner of the worst ASM module.

Nine further retained PCO sidecars (one pair each, every PCO exact):

| module | control user | candidate user | instructions ratio |
| ---: | ---: | ---: | ---: |
| 167 | 10.31 s | 9.12 s | 0.849 |
| 212 | 9.34 s | 8.13 s | 0.864 |
| 69 | 9.79 s | 8.41 s | 0.839 |
| 106 | 8.85 s | 7.91 s | 0.879 |
| 102 | 8.96 s | 8.11 s | 0.876 |
| 28 | 8.67 s | 7.60 s | 0.845 |
| 51 | 7.32 s | 6.57 s | 0.873 |
| 223 | 2.51 s | 2.42 s | 0.964 |
| 154 | 1.23 s | 1.22 s | 0.974 |

Population total 66.98 s -> 59.49 s user (1.126x). All numbers:
`build/tuple-noop-scan-v81-summary.json`; artifacts under
`build/tuple-noop-scan-pyast-replay/`, `build/tuple-noop-scan-module1-asm-replay/`,
`build/tuple-noop-scan-pco-population-replay/`.

## Focused gates

- `tests/python/test_tuple_publication_scan_counts.py` on the candidate
  archive: 22 passed (`build/tuple-publication-scan-counts-candidate-v81.log`).
  Both tuple tests now use `pytest.mark.pcc_gate(env="PCC_RUNTIME_ARCHIVE")`
  plus `pytest.fail` instead of the banned `pytest.skip`; 17 passed / 6
  deselected without the archive, 23 passed with it.
- GC0..4 production contract (`tests/python/gc_production_contract`, `-x -n0`
  per backend): 169 passed on each of GC0, GC1, GC2, GC3, GC4 with three
  pre-existing reds deselected (logs
  `build/tuple-noop-scan-gc-contract-deselect3-backend{0..4}-v81.log`).
- Strict runtime mirror/ABI gates (`test_freestanding_runtime_no_c_closure`,
  `test_runtime_archive_provenance`, `test_port_abi_constants`,
  `test_py_runtime_abi_attrs`, `test_atomic_mirror_gap`,
  `test_runtime_archive_consumers`): 178 passed, two pre-existing reds
  (`build/tuple-noop-scan-mirror-abi-gates*-v81.log`).

## Pre-existing HEAD reds attributed as independent of this change

Each reproduces identically on the pre-fix control archive, the candidate
archive and the frozen v80 bundle, or on untouched files:

1. `gc_production_contract/test_extension_module_state_roots` (5 items):
   `pcc self-link mode does not support native-extension export anchors`;
   passes with `PCC_SELF_LINK=cc`. Compile-stage, runtime-independent.
2. `test_valueclass_pointer_payload_updates_after_optional_relocation[4]`:
   relocation-set selection returns False on control, candidate and v80
   archives (`scratchpad/vc-reloc`).
3. `test_production_io_waitset_modes_preserve_roots[auto-2]`: the C probe
   hangs at the 30 s cap for backend 2 / mode auto on both the pre-fix and
   the current threaded C runtime; backend 4 / auto passes on both.
4. `test_runtime_high_substrate_tls_and_reentrant_lock_behavior`: rc=3 on
   control, candidate and v80 archives, 3/3 each.
5. `test_public_type_tags_are_never_reintroduced_as_raw_literals`: raw tags
   in `freestanding_gc_object_slots.py:537` and `py_int_parse.py:380/382/384`.
6. `tests/test_no_skip_doctrine.py`: 15 violations in other files.

Items 1-3 are routed to `GC-P2-BACKEND4-CONTRACT-REDS-BISECT`; items 4 and 5
receive their own rows; item 6 is `TEST-P1-NO-SKIP-DOCTRINE-REMAINING-FAMILIES`.

## Supported claim

On GC0, native counted tuple construction has linear completion-check work
with exact CPython-visible results, C and pcc-Python mirrors preserve the
store/refcount/tracking/GC4 publication contract (22 deterministic cases),
and the same compiler with the same input reduces the retained py_ast PCO
worker by 25% instructions / 1.25x CPU with byte-identical output and flat
RSS; eight other large PCO workers improve 12-16%.

## Not proven

No Stage2 or Stage3 has run on this source; no stage parity, fixed point,
GC1..4 stage or GC4-linearity claim follows. The ASM lane (worst module) is
unchanged within 0.7%, so this row does not close the 3.05x stage gap.
