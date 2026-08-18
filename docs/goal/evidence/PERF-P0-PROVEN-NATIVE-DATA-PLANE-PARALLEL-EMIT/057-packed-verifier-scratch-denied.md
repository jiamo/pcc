# Packed verifier scratch `[DENIED]`

## Proposal

Replace the ordinary verifier's per-function CFG buckets, predecessor and
successor list-of-lists, dominator work lists, PHI sets and switch sets with one
module-reused `_VerifierScratch` composed entirely of `CompilerIntArena`
columns.  The existing verifier remained the only rule and diagnostic owner;
no second validation implementation or raw pointer lease was added.

The measured v58 caller graph gave the complete touched family a 29.6% sample
ceiling: CFG 3.1%, dominators 1.1%, PHI 0.2%, ordinary uses 14.3%, definitions
5.7%, and terminators 5.2%.

## Correctness gates

- The dense dominator oracle, including a 100,000-block chain, matched the
  packed interval implementation.
- Missing/repeated PHI predecessors, non-dominating uses, missing targets and a
  bounded packed duplicate-switch table retained fail-closed diagnostics.
- Valid CFG verification left diagnostic projection counters at zero.
- Verifier/inventory focused gates passed (30 tests), the complete direct
  indexed file passed (17 tests), the 227-module contextual gate passed, and
  its current verifier IR emitted 7,540,212 bytes of self-backend assembly.
- Source-frozen v66 built a libSystem-only pcc1 in 167.77s wall / 684.44 tree
  CPU seconds / 4,859,822,080-byte process max RSS.

## Representative pcc1 result

One adjacent ABI-only-v65 control / packed-v66 candidate replay used the same
retained `module_1.direct.pidx` and produced byte-identical 61,075,757-byte ASM,
SHA-256 `9811ca4cb92aa9a471743bf845528e7005530b83d8c9af160691c8a44677b8ef`.

```text
metric                         v65 control       v66 packed         change
wall                              29.39s            30.76s          +4.7%
user+system CPU                   29.25s            30.70s          +5.0%
instructions                424,123,420,839   453,187,129,216       +6.85%
sampled tree peak             4,536,025,088     4,494,098,432       -0.9%
```

## Disposition

The small memory reduction cannot justify a stable CPU/instruction regression.
The scratch implementation, classification and tests were forward-removed;
the verifier, its focused test and the inventory contract again have zero diff
from the ABI-only v65 source.  No Stage2 ran.

This is the second result proving that replacing a Python container with an
arena is insufficient while each scalar access remains an out-of-line
pcc-Python method call.  Do not retry another arena container spelling.  The
next architectural slice must eliminate the getter/setter call protocol itself
through a generic compiler-owned inline/intrinsic representation, then return
to the complete verifier projection closure.

One unrelated stale test assumption was found while running the direct-indexed
file: total structured instructions now include generic arithmetic/branch
families, while the named unscaled/move/call counters are documented subsets.
The assertion now requires total >= subset sum and still checks exact assembled
sections/undefined symbols.
