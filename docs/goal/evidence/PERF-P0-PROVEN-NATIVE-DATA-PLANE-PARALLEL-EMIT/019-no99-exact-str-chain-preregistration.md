# No.99 exact-string concat-chain pre-registration

The No.89 whole-stage profile leaves a distributed object-lifecycle owner, not
another isolated 3--7% helper.  Existing controls close three tempting routes:

- allocation-point root elision is sound only in its historical tested slice
  and measured 1.022x because root stores themselves are about 1.9%;
- same-source LLVM/self pcc1 controls emit byte-identical item311 assembly and
  frontend IR, but differ by only 4.2% and 2.2% instructions respectively;
- list-backed `str.join` is 1.316x more instruction-heavy than a five-part
  chain and is denied.

The existing runtime allocation side channel then identified one coherent
representation family.  A watchdog-bounded partial real frontend worker
recorded 969,826 allocation requests: string was 68.55% of requests and
77.21% of requested bytes, with payload lengths 1--12 dominant.  List, dict,
and tuple together account for another 30.75% of requests.  The log is
diagnostic only; per-event I/O makes its wall time meaningless.

Static source sizing found 1,231 maximal 3+-operand addition chains, 1,182 of
length 3--8.  No.99 therefore targets one generic semantic rule: a maximal
3--8 leaf concat is fused only when every inferred leaf is exact `StrType`.
It uses one bounded no-list runtime ABI, preserves left-to-right evaluation and
owner cleanup, and leaves Dyn/user-class/two-part/unsupported chains on the
current path.  Full design, controls, gates, and the 1.15x frontend-worker
acceptance line are recorded in
`docs/investigations/pcc1-exact-str-concat-chain-object-tax.md`.

Claim correction made before candidate-worker measurement: item311 backend
assembly remains an exact-output gate, but the frontend worker compiles pcc
source containing admitted chains and therefore must change its IR.  Its gate
is two byte-identical candidate repetitions plus a structural diff confined to
the concat/root-lifetime substitution, followed by runnable pcc2/fixed-point
evidence.  Requiring candidate frontend IR to equal No.89 would require the
optimization not to execute and was internally contradictory.

No production compiler/runtime source changed in this evidence slice.  No
Stage2, Stage3, or GC1--4 gate ran.
