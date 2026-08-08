# Tier-2 scientific/build replacement gate source

Mode: host-side source/focused validation only. This is not current-pcc1,
GC0..4, fixed-point, Darwin/Linux, or long-running evidence.

Implemented `tests/integration/test_pcc1_scientific_build_replacement.py` as
one fail-fast Level-2 chain:

- hashes the 7,961-file clean NumPy 2.4.4 release tree by canonical POSIX
  relative-path order (`3ab6d97b...`), excluding local `.venv`, build and cache
  state;
- requires the canonical simplejson 4.1.1 sdist with SHA-256
  `c08eb9f7...` and performs both installs offline with `--build=owned`;
- poisons configured and PATH Python/pip/uv/pcc entrypoints and rejects any
  invocation recorded by the guard;
- asserts pcc-native/no-libpython package provenance, the owned Meson action
  chain, native extension artifacts, and absence of CPython-extension files;
- compiles one self/no-libpython application covering NumPy indexing,
  broadcasting, reduction, matrix multiplication and byte serialization;
- exercises simplejson's native scanner/decoder/encoder plus host-created
  gzip/bz2/xz/ZIP/TAR inputs and pcc-created streaming zlib/gzip/bz2/xz output;
- compares the behavioral result with a separate exact CPython 3.13.2 NumPy
  2.4.4/simplejson 4.1.1 oracle;
- executes the single artifact under GC0..4, asserts zero pin balance, samples
  RSS/wall time and records GC pause/root telemetry in a mode-labelled bounded
  report; and
- AST-audits package/compiler/runtime mechanisms for NumPy/simplejson equality
  branches rather than banning package-owned source names.

Focused validation performed:

```text
python -m py_compile (Tier-2 gate + catalog test): PASS
pytest -q -x -n0 ...::test_level_two_gate_is_one_hostless_owned_build_and_five_gc_chain:
1 passed in 0.09s
generated oracle and telemetry workload sources: AST parse PASS
clean NumPy tree digest: 3ab6d97b34440c2e5d02ed5458068533dfb72ac9372030cdd8daa0b55ce17525
generic package-name branch audit: PASS
git diff --check (focused files): PASS
```

Open: run the required current-source pcc1 gate with
`PCC_SIMPLEJSON_411_SDIST`, an exact CPython 3.13.2 executable/site, and the
immutable production runtime archive; fix its first failure using focused
reproducers. Then collect Darwin/Linux and mandatory 30-minute numerical,
throughput, RSS, pause and native-handle evidence. The bounded report is
explicitly marked `bounded_sample_only` and does not satisfy that release
envelope.
