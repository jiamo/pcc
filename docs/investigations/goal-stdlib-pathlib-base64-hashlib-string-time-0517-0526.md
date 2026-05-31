# goal native stdlib: pathlib / base64 / hashlib / string / time

This pack expands more self-host-friendly stdlib modules.

## pathlib

Adds more PurePath/Path helpers: fspath, joinpath, parts, suffixes, match,
with_name, with_suffix, read/write text/bytes.

## base64

Adds pure Python b64encode/b64decode and urlsafe variants.

## hashlib

Adds a pure Python SHA-256 implementation with `sha256`, `new`, `digest`,
`hexdigest`, `copy`, and update streaming.

## string

Adds `capwords` and a minimal `Template`.

## time

Routes `time()` and `monotonic()` through `pcc_runtime_now_us`, avoiding
struct-timespec marshalling.

Gate:

```bash
bash scripts/run_stdlib_more_goal_gate.sh
```
