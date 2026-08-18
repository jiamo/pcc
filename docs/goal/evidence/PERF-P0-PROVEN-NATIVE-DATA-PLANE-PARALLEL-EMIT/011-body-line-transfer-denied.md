# Function-body line transfer denied

## Proposal

Transfer `_iter_function_defs`' already-created body line list directly to
`_parse_blocks`, deleting the intermediate multi-megabyte `join` followed by
an immediate `splitlines`. No user semantics, ID order, diagnostics or
downstream pass changed.

The source-free CPython3.15 discriminator on exact item311 improved
0.237--0.240s to 0.107--0.108s and allocation peak 14,356,117B to 432B with
equal results. This authorized one pcc1 build but was not presented as a pcc1
claim.

## Correctness and frozen compiler

- focused parser/kernel/call/stackmap: 56 passed;
- strict parser self/no-libpython closure: rc0;
- host item311: exact `ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`;
- frozen source delta: exactly `pcc/backend/self_backend_parse.py`;
- pcc1: `104261e916323d0f31cbc0b233a58c7742a2cb4460ec0f35d0b86d3fc4f678c2`;
- host/runtime/mode: CPython 3.15.0rc1, GC0 archive `624e1de9...`,
  self/no-libpython, libSystem-only;
- Stage1: 265.07s wall / 1,060.72 CPU-s / 177.612B instructions /
  1,312,409,088B footprint.

## Paired result

```text
pair   wall B/C   CPU B/C   instructions C/B   footprint C/B   assembly
1       0.99925    0.99925       0.998758          0.983065      exact
2       1.00377    1.00454       0.998703          0.983079      exact
3       0.99775    0.99850       0.999175          0.983065      exact
median  0.99925    0.99925       0.998758          0.983065      exact
```

## Verdict

`[DENIED]`. The pcc1 worker gets a real 1.7% footprint reduction but no
throughput win and misses the registered 1.05x wall/CPU line. The production
parser is byte-identical to accepted No.89 after forward rollback. No Stage2,
fixed point or GC1--4 claim follows.
