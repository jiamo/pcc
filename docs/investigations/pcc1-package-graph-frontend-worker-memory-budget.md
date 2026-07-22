# pcc1 package-graph frontend worker memory budget

Resolved locally on 2026-07-22. After a clean locked NumPy install, the
wheel-owned pcc1 compiled the 114-module application graph with the generic
ten-worker frontend default and its parent RSS grew from roughly 7 GiB to 9.8
GiB and then 18.0 GiB. The run was terminated at the repository's 16 GiB
safety boundary and its entire process group was verified absent. It produced
no pytest summary and is not green evidence.

## Failure boundary

This was separate from the stale-source-build contamination recorded in
`uv-locked-local-source-build-contamination.md`: installation had succeeded,
the pcc-native extension set was clean, and the failure happened while pcc1
compiled the final application.

Closure and profile probes established:

- the installed and historical NumPy paths collect the same 81 initial Python
  modules;
- native extension ports and recursive stdlib expansion produce 114 modules
  when host stdlib location is available;
- the source set is about 2.09 MiB and generates 51,973,152 bytes of LLVM IR;
- `copyreg` is the one host-located module needed for isolated runtime startup,
  but compiled alone it produces only about 620 KiB of IR, so it is not a
  single-module explosion;
- wheel relocation is not causal: the wheel pcc1 compiles the 113-module
  no-host variant in 129.604 seconds.

## Root cause and policy

The generic frontend `auto` policy admitted ten isolated native workers for
every graph. That policy is effective for the compiler bootstrap, whose
module/IR shape is already measured and separately resource-budgeted, but a
package graph combines generated package sources, native-extension object
ports, and host-located stdlib providers. The ten-worker package run retained
allocator state beyond the unattended memory budget.

The fix preserves bootstrap's `auto=10` behavior and caps automatic frontend
concurrency at two only when the selected closure actually contains a package
root with a pcc package manifest. This is package-generic; it does not inspect
the distribution name. Explicit numeric `PCC_PY_FRONTEND_JOBS` remains
authoritative.

## Controlled proof

The same wheel pcc1, installed overlay, 114 modules, self backend, strict
no-libpython mode, and disabled-by-default self optimization-pass policy were
compiled with the two-worker budget. It completed in 167.987 seconds. Sampled
RSS stayed around 2.0 GiB for the parent plus at most two roughly 2.0--2.36 GiB
emitters, below 16 GiB. With both host Python and `PCC_PACKAGE_SITE` unavailable
at runtime, the artifact printed:

```text
2.4.4
[2, 3, 4]
```

`otool -L` showed only `libSystem`, with no libpython or Python dylib edge.
Focused unit coverage proves both the automatic package cap and the explicit
numeric override. The final locked-sync integration gate remains the required
end-to-end proof after rebuilding pcc1.

