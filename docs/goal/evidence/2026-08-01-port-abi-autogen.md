# Port ABI constants generated from the C headers

Date: 2026-08-01

Task: `ARCH-P2-PORT-ABI-AUTOGEN`

## The drift class this closes

The pcc-Python ports read object fields through byte offsets written as
literals — 140+ of them. Nothing connected those literals to the C headers,
so a C-side layout change reached the mirror only if a human noticed. That is
the shape behind the `py_gc_track` double-registration incident: the C side
moved, the port did not, and both compiled fine.

`tests/python/test_runtime_layout_contract.py` (2026-07-23) locks the layouts
the ports assume, which catches drift *after* it happens. This row asked for
the other half: make the headers the source, so the port constants cannot be
stale in the first place.

## What landed

`scripts/gen_port_abi_constants.py` compiles a probe against
`pcc/py_runtime/include` + `src` with the host cc, reads real
`offsetof`/`sizeof`/enum values, and writes
`pcc/py_runtime/py/py_abi_constants.py`:

```text
struct field offsets   33   (PyObjectHeader, PyInt/Float/Complex/Bytes/
                             ByteArray/MemoryView/Str/List/Tuple/Dict objects,
                             DictEntry, PyClassObject)
struct sizes            7
type tags              24   (enum PyTypeTag, including PY_TYPE_USER)
header flags            3   (FINALIZED / GC_TRACKED / IMMORTAL)
```

`--check` regenerates in memory and fails if the committed file differs, so a
header change that is not regenerated fails a gate rather than shipping.

## Verification: two independent sources must agree

`tests/python/test_port_abi_constants.py` (4 passed):

- the generated file is present and marked generated
- `--check` passes against the current headers (not stale)
- every generated offset/size is compared to the **hand-written**
  `EXPECTED_OFFSETS`/`EXPECTED_SIZES` in the layout contract test — two
  independently maintained sources, so if they ever disagree, one of them is
  wrong and the ports are reading the wrong bytes either way
- the tag/flag space is self-consistent: `PY_TYPE_STR == 4`, every builtin tag
  is below `PY_TYPE_USER`, every flag is a power of two

Sample of the agreement (generated vs contract): `PyClassObject.del_method`
96, `.attrs` 104, `.metaclass` 112; `PyStrObject.data` 40; `DictEntry` 24
bytes; `PyClassObject` 120 bytes.

## Supported claim

The port-side ABI constants are now derived from the C headers by a
generator, with a staleness gate and a cross-check against the independent
layout contract. The single source of truth the row asked for exists.

## Not proven

- The 140+ inline literals in `pcc/py_runtime/py/*.py` are **not** migrated to
  import from the generated module. The row explicitly scopes that as
  incremental ("then migrate port readers off inline literals
  incrementally"), and each migration changes the pcc-Python sources that the
  bootstrap compiles, so it needs its own slices with bootstrap gates.
- The generator covers the structures the ports mirror today, not every C
  struct in the runtime.

## Update: the migration half is blocked by a measured defect, not by effort

Before leaving "migrate the readers incrementally" as an open-ended boundary,
one reader was migrated as a probe: `py_list.py` imported
`PYLISTOBJECT_LENGTH_OFFSET` from the generated module and used it in
`py_list_len`. The port archive built cleanly, and then:

```text
a = [1, 2, 3]; print(len(a))    ->  NotImplementedError:
                                    no-libpython function unavailable:
                                    py_list.py_list_len
                                    (and len printed 0, not 3)
```

A cross-module constant import inside a port module makes the compiled port
function unavailable and silently yields zero — the same family as the
recorded "pcc-Python module-level int constants get zeroed in stripped
library .o builds; inline at use site" pitfall, and the same shape as the
47-fallback cross-module bridge that stopped the CLI helper dedup earlier
today. Reverted; the port is verified restored (`3 / 4 / 3`).

So the remaining half of this row is not a matter of grinding through 140+
literals: it is blocked on the port build's handling of cross-module
constants. Until that is fixed, importing the generated constants into a port
module makes the runtime *silently wrong*, which is worse than the literals.

The generator still earns its place: the constants are now derived from the
headers and gated for staleness, so the literals have an authoritative
reference to be checked against — and the layout-contract cross-check is what
catches drift today.
