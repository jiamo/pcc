# Bounded llvm-test-suite SingleSource corpus

This directory contains a verbatim, bounded snapshot of four C sources from
the LLVM test-suite.  It is a correctness corpus, not a benchmark suite.

- Upstream: `https://github.com/llvm/llvm-test-suite`
- Commit: `824802c01e93a8d49a77384da4e68c76d1021953`
- License: Apache-2.0 WITH LLVM-exception; legacy LLVM/NCSA terms are retained
  in `LICENSE.TXT` because these long-lived tests predate the relicensing.
- Selection: signed narrow arguments, aggregate copy, function pointers, and
  variadic calls including aggregates passed by value.

The filenames and file digests are pinned in
`tests/llvm_single_source_manifest.json`.  Do not add a source merely because
it happens to pass.  Every addition must name a distinct C semantic/ABI
surface, retain its upstream path and license, and fit the recorded wall-time
budget.  MultiSource programs and performance claims are deliberately out of
scope.
