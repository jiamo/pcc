# No.100 direct frontend-to-indexed-kernel pre-registration

No.89 phase alignment proves the remaining pcc1-specific gap is not export or
link scheduling.  Frontend codegen grows from 16.160s to 109.684s and native
emit from 96.323s to 254.655s; together they own 251.856s of the Stage2/Stage1
delta.  Export differs by only 2.307s and the pcc linker by 13.143s.

The downstream `IndexedFunctionSeed`/`IndexedFunctionKernel` plane already
owns final dense records.  The frontend `llvm_capi.ir` builder still collapses
structured operands to strings, so another process must parse hundreds of MB
back into that plane.  No.100 closes that exact boundary: the builder publishes
the neutral final seed while structured values are live, canonical text stays
as a lazy oracle/diagnostic projection, and a short-lived worker releases
frontend state before direct verify/prepare/emit.

The full design, exclusions, output/counter/RSS gates and pre-registered 1.25x
representative combined-worker threshold are recorded in
`docs/investigations/pcc1-frontend-direct-indexed-kernel-plane.md`.

No compiler source changed in this slice.  No Stage1, Stage2, Stage3 or GC1--4
gate ran.

