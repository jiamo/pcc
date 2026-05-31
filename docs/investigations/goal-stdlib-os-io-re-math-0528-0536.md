# goal native stdlib core: os/io/re/math

This pack expands core stdlib replacements used by pcc self-host and tests.

## os.path

Adds split, splitext, normpath, abspath, isabs, commonpath, isfile/isdir
fallbacks, plus path constants.

## io

StringIO and BytesIO now support seek/tell/readline and positional writes.

## re

Adds a literal-pattern subset for match/search/fullmatch/findall/sub/split.
This is not PCRE; it is enough to remove fallback for simple pcc call sites
until the PCRE2 backend lands.

## math

Adds pure helpers around existing libm externs: isnan, isinf, isfinite,
trunc, copysign, hypot, dist, radians, degrees, prod, factorial.

Gate:

```bash
bash scripts/run_stdlib_core_goal_gate.sh
```
