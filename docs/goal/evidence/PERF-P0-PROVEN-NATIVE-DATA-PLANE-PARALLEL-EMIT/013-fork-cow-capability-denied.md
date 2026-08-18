# Fork/COW compiler worker boundary denied by capability audit

## Question

Can pcc1 keep its fast single-thread runtime, parse/plan item311 once, then
fork copy-on-write children that emit deterministic block ranges without
serializing the native data plane?

## Evidence

- `pcc/py_stdlib/multiprocessing.py` states that pcc has no fork/spawn runtime.
- Darwin pcc-Python owns `posix_spawn`, pipe and waitpid; spawn executes a new
  process image and shares no parsed kernel heap.
- Linux unsafe process lowering's raw fork is an internal spawn/exec step, not
  a public continued-child Python execution ABI.
- No production source provides atfork handlers or child reset for allocator
  slabs, granule/radix metadata, GC object index, frame/root registries,
  exception TLS, locks or cache publication.
- Repository fork calls outside that spawn lowering are C probes.

## Verdict

`[DENIED]` without code. Continuing a managed heap after raw fork would be an
unspecified runtime/GC boundary. Exec-based workers must serialize or reparse
the 5.1MB kernel and therefore repeat the known dominant work. This evidence
supports neither parallel Stage2 nor a fixed point; GC1--4 remain deferred.
