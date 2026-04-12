"""pcc.py_stdlib.copy — shallow + deep copy skeleton."""
from __future__ import annotations


def copy(x):
    """Shallow copy."""
    if isinstance(x, list):
        return list(x)
    if isinstance(x, dict):
        return dict(x)
    if isinstance(x, tuple):
        return tuple(x)
    if isinstance(x, set):
        return set(x)
    # Fallback: if the object exposes ``__copy__``, use it.
    if hasattr(x, "__copy__"):
        return x.__copy__()
    return x


def deepcopy(x, memo=None):
    """Recursive deepcopy over list/dict/tuple/set + user objects with
    ``__deepcopy__``. Shared references are tracked via ``memo`` so
    cycles don't blow the stack."""
    if memo is None:
        memo = {}
    oid = id(x)
    if oid in memo:
        return memo[oid]
    if isinstance(x, list):
        out: list = []
        memo[oid] = out
        for item in x:
            out.append(deepcopy(item, memo))
        return out
    if isinstance(x, tuple):
        return tuple(deepcopy(item, memo) for item in x)
    if isinstance(x, dict):
        out_d: dict = {}
        memo[oid] = out_d
        for k, v in x.items():
            out_d[deepcopy(k, memo)] = deepcopy(v, memo)
        return out_d
    if isinstance(x, set):
        return set(deepcopy(item, memo) for item in x)
    if hasattr(x, "__deepcopy__"):
        r = x.__deepcopy__(memo)
        memo[oid] = r
        return r
    return x
