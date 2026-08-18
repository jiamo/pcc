# Lazy verifier diagnostics `[DENIED]`

## Proposal

The retained v58 raw pcc1 sample attributes 49.6% inclusive time to indexed
verification, including 16.1% in `_verify_ordinary_uses`.  The valid path still
looked up SSA value names and constructed per-instruction diagnostic context
strings even though those objects are observed only when verification fails.
The candidate delayed every such name/context projection in definitions,
calls, fixed records, GEPs, ordinary uses, PHIs, terminators and inline error
edges until the exact failing branch.  Unsupported and malformed IR retained
the original diagnostic path.

## Correctness and boundary gates

- A RED/GREEN ratchet made `IndexedFunctionKernel.value_name` raise during a
  valid direct-kernel verification; the candidate completed without reaching
  it.
- The complete verifier diagnostic suite plus direct inline-error-edge and
  record-inventory gates passed: 29 tests.
- Strict self/no-libpython closure for `self_backend_verify.py` passed.
- The 227-module direct-publication contextual gate passed in 45.12s.
- Source-frozen v61 built pcc1 successfully, passed the function compile/run
  smoke, and linked only libSystem.  The build stayed below the 8 GiB breaker
  (sampled tree peak 4,828,807,168B).

## Representative pcc1 result

One adjacent v58-control/v61-candidate pair used the same retained 14,911,544B
`py_ast` sidecar (`module_81.direct.pidx`) and produced byte-identical PCOs,
SHA-256 `2f0f6fa3e03c655403a28b0976efc8f33d6234c07519898125f0e846f257dd56`.

```text
metric                         v58 control       v61 candidate      change
wall                              17.40s             17.34s         -0.3%
user+system CPU                   17.23s             17.20s         -0.2%
instructions                262,097,386,039    261,463,933,969      -0.24%
process max RSS              1,590,050,816      1,582,825,472       -0.45%
sampled tree peak            1,574,961,152      1,575,829,504       +0.06%
```

The inclusive verifier profile did not represent diagnostic-projection self
cost.  Removing the projection is exact but immaterial; the expensive work is
below the verifier in generic runtime/GC protocol.  This does not meet the
architectural slice's required material CPU/RSS transfer.

## Disposition

The entire candidate and its ratchet were forward-removed.  The two production
and test files again have no diff from accepted v58.  Do not retry lazy
verifier diagnostic spellings as a Stage2 performance proposal.  The next
candidate must remove a complete emitted object/GC protocol family; the raw
sample attributes 50.4% self time to GC/refcount runtime rather than verifier
algorithm work.

