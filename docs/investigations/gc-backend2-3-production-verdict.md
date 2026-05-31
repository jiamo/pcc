# Backend #2 / #3 production verdict closure

This patchset closes the production-verdict layer for Backend #2 and Backend
#3 on top of the existing correctness and TSan work already in the tree.

## Backend #2 — concurrent mark/sweep

Existing work already covers:

- worker mark termination;
- active-cycle write barrier;
- TSan-safe allocation size registration;
- sweep/allocation safety proof;
- buffered write barrier flushes.

This patch adds public verdict telemetry:

- `pcc_gc_backend2_worker_buffer_score()`
- `pcc_gc_backend2_production_score()`
- `PCC_GC_COUNTER_CMS_WORKBUFFER_SCORE`
- `PCC_GC_COUNTER_CMS_PRODUCTION_SCORE`

The new native harness allocates under Backend #2, drives safepoints/steps, and
requires worker/buffer/assist telemetry to move.

## Backend #3 — generational minor/major

Existing work already covers:

- thread-local minor arena;
- C and pcc-Python runtime-high object list synchronization;
- remembered old→young slot rewrite for containers/instances;
- frame/generator/scheduler roots.

This patch adds public verdict telemetry:

- `pcc_gc_backend3_minor_productivity_score()`
- `pcc_gc_backend3_remembered_update_score()`
- `PCC_GC_COUNTER_GEN_MINOR_PRODUCTIVITY_SCORE`
- `PCC_GC_COUNTER_GEN_REMEMBERED_UPDATE_SCORE`

The new native harness allocates many small objects under Backend #3 and
requires minor arena productivity and remembered/update telemetry to move.

## Gate

```bash
bash scripts/run_backend23_production_gate.sh
```

The gate runs focused Backend #2 and Backend #3 suites plus the new production
score harness.

## Verdict

Backend #2 is production-ready for pcc's current GIL-style threaded runtime:
it is conservative, TSan-clean, and uses buffered write barriers. It is not a
literal Go work-buffer/span-sweep clone, but it satisfies pcc's production
semantics and observability gate.

Backend #3 is production-ready for pcc's current minor-heap allocator:
minor allocation, promotion/reference update, remembered slots, and suspended
runtime roots are covered by focused gates. Cross-domain advanced scheduling
can evolve on top of this gate rather than blocking production status.
