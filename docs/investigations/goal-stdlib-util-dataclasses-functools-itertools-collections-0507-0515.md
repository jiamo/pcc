# goal native stdlib util surface

This pack expands native stdlib modules commonly used in self-host and
pure-Python package imports.

## dataclasses

Removes the `exec`-based fallback and adds a native-friendly generic
`__init__(*args, **kwargs)` implementation, `Field`, `fields`, `asdict`,
`astuple`, `replace`, and `make_dataclass`.

## functools

Adds `update_wrapper`, metadata-preserving `wraps`, cache_info/cache_clear for
`lru_cache`, `cached_property`, `total_ordering`, and `cmp_to_key`.

## itertools

Adds `chain.from_iterable`, `accumulate`, `takewhile`, `dropwhile`, `starmap`,
`compress`, `zip_longest`, `tee`, and `permutations`.

## collections

Expands `Counter`, `deque`, and adds `ChainMap`.

Gate:

```bash
bash scripts/run_stdlib_util_goal_gate.sh
```
