# GC4 A3b relocation-reseed bounded page commit

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b holder sub-boundary confirmed; parent task remains
`IN_PROGRESS`.

## Claim boundary

Relocation-epoch reseed in the C and strict pcc-Python roots now computes the
evacuation-page byte aggregates from the already-authoritative evacuation list
in batches of at most 16 nodes per graph-lock tenure. It no longer detaches that
list, walks every zpage, repeatedly searches the relocation set, or rebuilds
membership under one unbounded lock tenure.

The cursor is globally published and every page-node unlink advances it before
the node or detached page storage can be recycled. A page pointer is loaded,
read and discarded entirely while the graph lock is held; no raw page pointer
survives an inter-batch unlock. Page-list or relocation-list revision changes
restart the complete aggregate. A concurrent full reset also prevents final
publication until its reset owner exits.

After the relocation aggregate stabilizes, a reseed commit owner rejects new
candidate admission and Backend-4 relocation/forwarding commits until the page
snapshot is either restarted or published. The owner remains held across a
revision restart, while object freeing and full reset remain able to make
progress and invalidate the snapshot.

This supersedes the earlier detach/rebuild commit mechanism. The already-proven
private evacuation-node preparation and its deterministic plan-growth/OOM
failure contract are retained conservatively as an admission plan; prepared
nodes are released after the stable snapshot and are not used to replace the
authoritative list.

This closes the relocation-reseed page holder only. GC3 promotion and
remembered-owner holders, decref/callback/log holders, A3c graph-lock no-park,
raw container transactions and collector-owned STW remain open.

## Genuine RED

`test_relocation_reseed_page_commit_is_bounded_without_raw_page_escape` was
added before implementation and failed on the absent commit owner:

```text
AssertionError: assert 'pcc_gc_backend4_reseed_commit_owner' in ...
1 failed in 0.34s
```

## Implementation

- C and strict state add `pcc_gc_backend4_reseed_commit_owner`; the strict
  runtime ABI and exact raw-global inventories include only the modules that
  consume it.
- Candidate selection, relocation-copy admission, forwarding-plan preparation
  and forwarding commit fail closed while the Backend-4 commit owner is live.
- Reseed scans the authoritative evacuation-node list through the existing
  unlink-aware page cursor in 16-node batches. Each batch compares both page
  and relocation revisions before it may continue or publish.
- A revision change or active full reset clears aggregate cursors, unlocks and
  safepoints, then restarts the relocation and page aggregate while retaining
  the commit owner.
- The deterministic probe phase bitmask now assigns bit 4 to the page scan.
- Obsolete C and strict private `detach_all` helpers were removed after all
  production callers disappeared.

## Focused evidence

All pytest commands stopped at the first failure. The final source identity was
recorded before the packet in
`build/gc4-a3b-reseed-page-source-identity.txt`.

1. A deterministic C/strict pthread probe selects 24 distinct medium pages,
   pauses after page node 16, concurrently performs full reset/unlink/recycle,
   then resumes. Both roots restart to zero without UAF and recover all 24
   candidates/pages and 1,440,000 bytes on reselection.

2. The final packet covered the source/order contract, strict selector,
   forwarding and relocation-copy ownership, repeated authoritative-page
   reseed, phase-4/phase-2/phase-1 reset interleavings, forced plan growth and
   allocation failure, four-thread reset/reseed, and C/strict relocation target
   phase parity:

   ```text
   gtimeout 360s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short <19 focused node ids> 2>&1 | tee build/gc4-a3b-reseed-page-final.log'
   19 passed in 132.73s
   ```

3. The five affected strict modules compiled directly with
   `--backend self --python-libpython=off --ir-scaffold=on --python-library`.
   Their LLVM receipts are hashed in
   `build/gc4-a3b-reseed-page-closures-final.log`.

4. Python syntax, C syntax with `PCC_WITH_THREADS=0/1`, and
   `git diff --check` passed. The C checks retained only pre-existing unrelated
   unused-function warnings; the obsolete reseed detach/count warnings are gone.

An attempted all-tests invocation of three strict ownership files was stopped
after 66 seconds because it had only dots and entered a cold archive fixture
without node-level evidence. It reported four completed passes before the
interrupt, left no compiler/pytest child, and is not used as green evidence.

## Frozen identities

```text
76606653bdb4f871c9d37d8c6f540f7b1e320566c6bd47bbc0057c9cc98248f6  pcc/py_runtime/src/py_gc_backend.c
251dcdb93d080e256de0072ee9a4e163f27302e7d76f73814d0b818cf475edb7  pcc/py_runtime/include/py_runtime.h
7e856e3abbf8503e094af7b80956a99e326e75d55c08c3e180131dc1e67aeb08  pcc/py_runtime/py/py_gc_backend.py
a2d8abbd697786cf67a820bb8a96341559ee48114956667c872296394241dadf  pcc/py_runtime/py/freestanding_gc_state.py
289d98b3d50df1ac5dd433f623dafc284ec65f037a1098612e8f6f9c9ba7ebdd  pcc/py_runtime/py/freestanding_gc_relocation_selector.py
16694ab40d03941abd73720667924618e5dca3a6f3508fd7129e4cd2cfec6cd7  pcc/py_runtime/py/freestanding_gc_forwarding_identity.py
7fed1d1567243996a503846acef45849e8d29217e2a77df7e9f6be140d8fead3  pcc/py_runtime/py/freestanding_gc_relocation_copy.py
46a19908d785a160de945473fc653e5e3cd8805374ccab37dd4b29b33c02e285  pcc/py_frontend/codegen/runtime_abi.py
315c496370241d7043b27a083fa432e7e76cb9eb3d9cd50f5aa46a0db255205c  tests/python/test_gc_backend4_production.py
d2f86b413a9aac4dc5b2245ea72fe5bbd2c691123ef670d6f3ee7778b79d2667  tests/python/test_freestanding_gc_relocation_selector.py
568642637af2be8c185f3bb6d033917c688de460beee373c97fedeab8ef848af  tests/python/test_freestanding_gc_forwarding_identity.py
cf9c354837762212b77061b354223d77d86ae5495a4230fcad676e0754f97b40  tests/python/test_freestanding_gc_relocation_copy.py
7d6de701dc1230356b7d77dda2bb5b47f4148ff8512495b78d1f1f3de882c269  build/gc4-a3b-reseed-page-final.log
9f738ee4c41248c162d3db32e02f715db565a735209e07abfd4bcc7e26e69fa3  build/gc4-a3b-reseed-page-closures-final.log
7d646f19426d645cd9a969fab353678a1f84244b5ce7f791f026dd6b471bd232  build/gc4-a3b-reseed-page-source-identity.txt
```

## Next boundary

Do not connect A3c yet. Inventory and split the remaining GC3 promotion and
remembered-owner graph-lock holders, including their safepoints, cleanup
decrefs and extension-root callbacks, before changing graph-lock/no-park
semantics.
