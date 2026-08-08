# Generic current-pcc1 package ABI matrix source

Mode: source/static evidence only. No current-pcc1 package matrix was run.

Added the missing required integration gate
`tests/integration/test_pcc1_generic_package_abi_matrix.py`. It composes the
existing package-name-independent installer with three capability profiles:

- pinned pure-Python `wheel` artifact, explicitly not promoted to a native
  extension claim;
- pinned real `simplejson==4.1.1` cold source build with pcc-owned build
  provenance and its native `_speedups` behavior;
- the pcc-native NumPy site and array computation.

Each application is compiled once by a receipt-current pcc1 in strict
self/no-libpython mode, checked for no libpython edge, and executed under
GC0..4 with byte-equal output. Package manifests are asserted separately so a
pure install, pcc-native source build, and native-extension import cannot be
conflated.

Static source audit of `pcc/py_frontend`, `pcc/py_runtime`, `pcc/package`, and
`pcc/cli_bootstrap.py` found no equality/switch on NumPy, simplejson,
pyahocorasick, or immutables. The only hit was a non-dispatch explanatory
comment about simplejson in dependency-closure code.

`py_compile` passed. The integration gate remains unrun until one deliberate
current-source pcc1 is available after shared source freeze.
