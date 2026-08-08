# CPython replacement contract

Mode: control-plane contract only.  This evidence freezes what a future
`pcc1` replacement release must prove; it does not claim that any target or
workload is implemented today.

The v1 matrix pins CPython 3.13.2, Darwin arm64 with a named libSystem boundary,
Linux x86_64 with a static zero-libc boundary, all five GC backends, three
cumulative release levels, 31 owned compatibility surfaces, and a cumulative
workload catalog.  Every surface has an owner, oracle, gate, verdict,
implementation status, and stable diagnostic policy.  Level 3 remains the
final product goal.

`pcc.cpython-replacement.evidence.v1` is now executable policy rather than a
prose checklist.  It binds the contract and workload digests, clean source,
pcc0/pcc1/pcc2/pcc3 artifacts, normalized pcc2/pcc3 identity, one application
artifact per cumulative workload, the full workload × target × GC run matrix,
process tree, linkage, pcc-Python runtime archive, pcc1-owned package artifacts,
performance result identity, and a separately executed CPython oracle.  It
fails closed on host/CPython owners, host-built packages, libpython,
CPython-extension ABI linkage, LLVM runtime fallback, dirty or partial
evidence, false Darwin zero-libc claims, and false Linux dynamic linkage.

Review and gates:

- Manual matrix/workload/roadmap audit found all 31 surface owners present in
  the task board and confirmed exact cumulative levels and target boundaries.
- `tests/python/test_pcc1_cpython_replacement_contract.py`: 16 passed.
- Contract plus workload-catalog gates: 46 passed.
- Evidence tests include Level-2 Cartesian coverage, host owner/package
  rejection, libpython rejection, digest binding, and pcc2/pcc3 normalized
  fixed-point rejection.
