# M1 real extension canary selection evidence

Date: 2026-07-11

Task: `M1-PKG-CANARY-SELECTION`

## Result

Selected and pinned `simplejson` 4.1.1 as the M1 real source C-extension
canary. The selected sdist is external to the repository, uses one extension
translation unit, and exercises PEP 489 `Py_mod_exec` with per-module state.
The machine-readable pin is `docs/goal/m1-package-canary.json`; the checked
comparison and claim boundary are in
`docs/reports/m1-package-canary-selection.md`.

## Source identity

```text
URL: https://files.pythonhosted.org/packages/source/s/simplejson/simplejson-4.1.1.tar.gz
SHA-256: c08eb9f7a90f77ae470e19a07472e9a79ebc0d1c2315d86a72767665bd5ba79f
```

`shasum -a 256` matched the pin. The other bounded candidates were
`immutables` 0.21 and `pyahocorasick` 2.3.1. Only `simplejson` met the required
PEP 489 or `PyType_FromSpec` selection criterion.

## Observed boundaries

- build: blocked at `unknown type name 'PyUnicodeWriter'`, a generic public
  Python 3.14 C-API gap;
- link: not reached until the compile gap is closed;
- module init: PEP 489 source markers are present and the generic runtime path
  exists, but package init is not reached yet;
- behavior: the compiled CPython source oracle passed nested dict/list/string
  dumps-loads, but the same boundary is not yet reached by pcc.

These boundaries are deliberately not promoted into a pcc build/import claim.

## Gates

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_m1_package_canary_selection.py \
  tests/python/test_no_numpy_special_cases.py
8 passed in 0.28s

gtimeout 180s env -u LC_ALL REQUIRE_SPEEDUPS=1 uv pip install \
  --target /tmp/pcc-m1-simplejson-oracle --no-cache \
  /tmp/pcc-m1-canary-probe/simplejson-4.1.1.tar.gz
PASS: built and installed simplejson 4.1.1 C extension under CPython 3.13

CPython oracle
simplejson.encoder.c_make_encoder is simplejson._speedups.make_encoder: True
encoded: {"items":[1,"two",null],"ok":true}
loads(encoded) == input: True

Black on the new Python gate and JSON parse validation
PASS
```

The source guard reads the selected distribution from the pin and proves that
the name is absent from compiler/runtime dispatch roots. No GCC torture or
full GCC validation was run.
