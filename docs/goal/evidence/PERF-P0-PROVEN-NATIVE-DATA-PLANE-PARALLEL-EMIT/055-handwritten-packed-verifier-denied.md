# Handwritten packed verifier `[DENIED]`

## Proposal and measured mechanism

The retained v58 module-1 caller graph placed 43.6% of its sampled window
under the ordinary self-IR verifier.  A pcc-Python raw-plane prototype consumed
the decoded `CompilerIntArena` payloads directly, built CFG/dominator scratch in
one raw allocation, returned `False` for unowned shapes, and replayed the
ordinary verifier for exact diagnostics.

The mechanism is real.  On the same frozen `cli_bootstrap` sidecar and exact
ASM output (`9811ca4c...`):

```text
metric                         v58 control       v62 prototype      change
wall                              29.25s             26.51s          -9.4%
user+system CPU                   29.09s             26.42s          -9.2%
instructions                424,136,701,126    381,962,715,897       -9.94%
process max RSS              4,595,859,456      4,357,832,704        -5.2%
sampled tree peak            4,566,138,880      4,293,001,216        -6.0%
verifier caller share              43.6%               3.9%
```

The 532-function `py_ast` sidecar passed with
`PCC_REQUIRE_NATIVE_INDEXED_VERIFY=1`, produced an exact PCO (`2f0f6fa3...`),
and moved only 17.23->16.79s CPU and 262.097B->255.793B instructions (about
2.5%/2.4%).  Module 1 retained two explicit ordinary-verifier fallbacks, both
for the same four cold `extractvalue` records in `_fnv1a_update_{u64,bytes_u64}`.

## Why the production shape is denied

The prototype duplicated the complete CFG, dominator, definition, operand
type, use, PHI, terminator and inline-error-edge rule set: 1,553 new production
lines beside the 1,522-line diagnostic oracle.  Code-converge found successive
false-accept or downstream-misinterpretation classes:

- instruction-kind and metadata lanes were not cross-checked;
- payload operands and serialized use facts could disagree and bypass
  dominance;
- terminator payload and terminator-use facts could disagree;
- call/constant text IDs were not range checked;
- post-stackprep slot/register state was accepted;
- branch conditions could name an undefined value;
- multiple PHIs reused one predecessor stamp;
- return/switch operand types were not checked;
- switch duplicate checking was unbounded quadratic work.

Focused tests caught and repaired those specific cases, but each repair
expanded a second handwritten semantics owner.  The review could not establish
a stable locality rule guaranteeing future verifier changes update both
implementations.  Record inventory also did not own the new lifetime-sensitive
raw-address consumer.

The source-frozen v62 build predates the final review fixes, so it is not a
candidate receipt.  It is nevertheless a regression warning against spending
a Stage2: v58->v62 Stage1 moved 164.88->171.53s wall,
673.69->693.17s tree CPU and 91.262->92.699B coordinator instructions; pcc1
grew 505,488 bytes.  These are not an adjacent formal A/B, so no isolated
Stage1 regression percentage is claimed.

## Disposition and next design

No Stage2 was run.  The handwritten fast verifier, raw-address accessor,
duplicate metadata and its dedicated tests were forward-removed; existing
production files again match accepted v58.  The measurements remain useful:
they prove verifier object protocol is removable, but not by cloning its
semantics.

The next admissible design has one canonical packed-verification rule source,
mechanically shared/specialized into object-oracle and raw execution.  The raw
side returns a compact failure code/index and the existing adapter alone
materializes names and exact diagnostics.  Any raw-span lease must be
kernel-owned, inventory-registered and counted; unsupported/cold shapes remain
explicit fallback rather than silent acceptance.

