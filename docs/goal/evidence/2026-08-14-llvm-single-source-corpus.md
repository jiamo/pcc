# LLVMREF-P3-TEST-SUITE-CORPUS — bounded SingleSource differential

Mode: host pcc and the platform C compiler. This is a bounded correctness
corpus, not llvm-test-suite-wide coverage or a performance claim.

The checked manifest pins four verbatim `llvm-test-suite` SingleSource files
and their license to commit `824802c01e93a8d49a77384da4e68c76d1021953`.
The strict loader proves the corpus is non-empty, digest-equal, feature-complete,
free of unlisted C files, outside Benchmarks, and bounded to 60 seconds. The
integration gate compiles the identical source with pcc and the native compiler
in parallel, fails at the first case, and compares exit status, stdout and
stderr.

```text
gtimeout 30s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/c/test_llvm_single_source_corpus_contract.py
5 passed in 0.06s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 -m integration \
  --tb=short tests/c/test_llvm_single_source_corpus.py
1 passed in 1.66s
```

The four cases cover signed narrow arguments, aggregate copy, function
pointers and aggregate varargs. MultiSource, benchmarks, unsupported language
features and whole-suite adoption remain outside this completed finite row.
