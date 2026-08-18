# The "GC3 Stage2 failure" was ensure_runtime staleness under a live worktree, not a GC3 bug

Date: 2026-08-27
Tasks: `GC-P0-FIVE-GC-MATURE-RESOURCE-EFFICIENCY` (gating context),
`PERF-P0-STAGE2-COLD-CACHE-REGRESSION` (measurement hygiene)
Claim level: retraction of a wrongly framed regression + one mechanism,
substitution-tested. No GC3 verdict is made here; the clean gate is running.

## What was observed, and wrongly framed

`PCC_GC_BACKEND=3` Stage2 on the validated batch77 pcc1 failed twice
(`rc=1`, ~537 s, empty `PyPipelineError`), while GC0 on the same binary had a
green fixed point. This was initially framed as a GC3 regression.

## The actual mechanism

One timeline explains every observation:

```text
09:1x-09:4x  batch77 GC0 chain GREEN            (no newer sources existed)
11:11:28     ownership_lowering.py edited        (parallel session, linker lane)
11:14:02     runtime archive rebuilt
11:1x+537s   GC3 stage2 reaches ensure_runtime  -> compiler_sources_newer fires
12:11:30     macho_link.py edited                (archive permanently "stale" again)
12:2x        GC0 SMOKE fails identically         <- kills the GC3-specific framing
```

`ensure_runtime`'s `compiler_sources_newer_than(archive)` check sees any
compiler-source edit made after the archive build, declares the archive stale,
and starts a nested runtime self-rebuild inside the compile — which then dies
under the run's watchdog. A GC0 smoke failing the same way, and the
substitution test (`PCC_RUNTIME_ARCHIVE=<archive>` pinned: the same GC3
compile sails past the death point with zero errors), close the case.

This is the AGENTS "do not edit source a running measurement depends on"
trap, in its cross-session form.

## Operational rule while two lanes share the worktree

Every archive-dependent probe or gate on the GC lane must pin
`PCC_RUNTIME_ARCHIVE` to a self-consistent archive, making it immune to the
linker lane's source edits. The pinned-archive GC3 gate is running now.

## Residual diagnostic hole (small, real)

The death path reports `PyPipelineError` with EMPTY str and `args=None`
through a wrapper that does `raise PyPipelineError(str(exc) or
type(exc).__name__)` — that expression cannot produce an empty message on
host semantics, so the text is being lost pcc-native-side somewhere between
raise and print. Also `exc.args is None` is not a CPython-legal state. Filed
as a diagnostic-integrity task; it cost four probe rounds today.


## Appendix: two wrong death-diagnoses made while running the clean gate

Recorded because each cost a relaunch and one nearly became a wrong claim:

1. The GC3-stage3 probe was twice declared dead ("log 0 bytes, no processes")
   and once blamed on the parallel session's cleanup. Reality: a non-verbose
   pcc compile prints NOTHING until the final link line, so an empty log is
   the NORMAL mid-run state, and the `nohup ... &` processes from this
   harness's transient shells are unreliable — the same launch runs fine
   foreground (`rc=124` at a 30 s probe cap, i.e. alive) and under the
   harness's tracked background. Empty-log + missing-from-ps is only proof of
   death when the process was launched in a way that survives the shell.
2. The GC3-built pcc2 was briefly declared broken ("exits 0 instantly").
   Reality: the `| head -5; echo rc=$?` pipeline captured `head`'s exit
   status, not the compiler's. The binary compiles and runs a smoke correctly
   under both GC0 and GC3 (`42`), and byte-produces a 2.2 MB output.

Both misreads share one lesson the repo already teaches: silence is not a
verdict — capture the exit status of the process itself, foreground, before
declaring anything dead or broken.
