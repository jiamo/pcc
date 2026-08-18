# Borrowed integer span `[DENIED]`; imported valueclass method ABI fixed

## Proposal

The existing semantic verifier was changed to borrow immutable three-word
`CompilerIntSpan` values from its packed integer arenas.  Its hot call/fixed/GEP
checks read scalar records through those spans while retaining the ordinary
verifier as the only rule and diagnostic owner.  This avoided the duplicated
1,553-line verifier rejected by evidence 055.

## Correctness defect exposed and fixed

The first source-frozen build failed before producing pcc1.  A valueclass used
as a top-level parameter already had the correct `{i64,i64,i64}` ABI, but an
instance-method declaration imported from another module still hard-coded its
receiver as `ptr`.  The resulting call passed the aggregate to a `ptr`
declaration and the self verifier rejected it.

The generic export contract now records the declaring valueclass projection as
the receiver type for every non-static method, and extern class declarations
consume that descriptor instead of unconditionally selecting `ptr`.  The
regression uses an aliased cross-module import, verifies both definition and
call signatures, and sends the resulting IR through the self backend.  This is
a general valueclass ABI correction; it contains no `CompilerIntSpan` name
special case.

Focused evidence after the fix:

- 52 export/valueclass/verifier/arena/inventory/indexed-codec tests passed;
- the 227-module Stage1 contextual gate passed;
- current `self_backend_verify` IR used aggregate receivers at every
  `CompilerIntSpan_get*` call and emitted 8,068,750 bytes of self-backend
  assembly;
- ABI-only source-frozen v65 built a libSystem-only pcc1 in 163.24s wall /
  669.65 tree CPU seconds / 4,856,299,520-byte process max RSS.

## Representative pcc1 result

The complete span candidate v64 built successfully in 168.88s wall / 681.28
tree CPU seconds.  One adjacent v58-control/v64-candidate replay used the same
retained `module_1.direct.pidx` and produced byte-identical 61,075,757-byte ASM,
SHA-256 `9811ca4cb92aa9a471743bf845528e7005530b83d8c9af160691c8a44677b8ef`.

```text
metric                         v58 control       v64 span          change
wall                              29.43s            31.55s          +7.2%
user+system CPU                   29.24s            31.39s          +7.4%
instructions                424,380,738,256   454,622,461,896       +7.1%
sampled tree peak             4,536,008,704     4,666,277,888       +2.9%
```

The earlier isolated 20-million-read microbenchmark was real but misleading at
the complete call graph: passing nine three-word aggregates through verifier
helpers costs more than the receiver-object reads it replaces.

## Disposition

`CompilerIntSpan`, its verifier rewrite, inventory row and arena tests were
forward-removed; those four files again have zero diff from the accepted v58
source.  No `py_ast` transfer or Stage2 was run after the representative
regression.  The independent imported-valueclass method ABI fix and regression
remain because v65 proves the corrected compiler builds without the span
regression.

The projection-closure task remains open.  Do not retry multi-word borrowed
spans across helper boundaries.  The next admissible design must keep the raw
lease kernel-owned and pass scalar addresses or execute inside one owner while
retaining one canonical rule source and exact cold diagnostics.
