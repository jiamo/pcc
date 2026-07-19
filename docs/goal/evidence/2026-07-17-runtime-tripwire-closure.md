# AUD-P0-RUNTIME-ASSERTS closure evidence

## Outcome

The finite open boundary is closed.  `PCC_RT_TRIPWIRE` remains inert by
default and now covers the named zpage/UNKNOWN-forwarding, scheduler root,
continuation root, and native-handle lifetime invariants.  An armed runtime
probe proves both the valid path and a deliberately corrupted native-handle
path; the latter aborts with the expected runtime tripwire diagnostic.

The native-handle probe exposed and fixed a real C-runtime lifetime bug:
`py_type_tag_is_valid()` omitted `PY_TYPE_CPY_HANDLE`, so `py_decref()` returned
before dispatching its deallocator and leaked the foreign reference.  The tag
is now valid and the probe proves that the registered release hook runs exactly
once.  The pcc-Python mirror already accepted the full tag range through 32.

Default builds retain zero-cost behavior: conditions remain inside the
inert-default macro, and scheduler-only predecessor bookkeeping is compiled
only under `PCC_RUNTIME_TRIPWIRES`.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/python/test_runtime_tripwires.py`
  — **3 passed in 6.84s**.  This builds an isolated armed archive, exercises
  valid backend-4 relocation/root/native-handle paths, and proves fault
  injection terminates with the named tripwire log.
- `env -u LC_ALL make -B -C pcc/py_runtime libpy_runtime.a`
  — **exit 0**.  The repository archive was rebuilt in the default,
  tripwires-off configuration after the armed isolated build.

No full GCC suite or bootstrap matrix was run: neither is needed to prove this
finite runtime-assertion boundary, and the focused C probes execute the changed
paths directly.

