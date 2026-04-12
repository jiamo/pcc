"""pcc.py_stdlib.functools — narrow skeleton for the self-host path.

``wraps``, ``lru_cache`` (LRU-free in this scaffold — just caches
all calls), ``reduce``, ``partial``. Full-strength LRU eviction +
typed cache key generation is P6C.4 work.
"""
from __future__ import annotations


def wraps(wrapped):
    """Minimal @wraps — returns the inner function unchanged. The
    attribute copies ``__name__`` / ``__doc__`` that real wraps does
    aren't yet respected by pcc's codegen, so emitting them would be
    noise."""
    def _decorate(fn):
        return fn
    return _decorate


def reduce(fn, iterable, *initial):
    it = iter(iterable)
    if initial:
        acc = initial[0]
    else:
        acc = next(it)
    for item in it:
        acc = fn(acc, item)
    return acc


class partial:
    """Skeleton ``functools.partial`` — binds leading positional args."""

    def __init__(self, fn, *args, **kwargs) -> None:
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def __call__(self, *more_args, **more_kwargs):
        kw = dict(self._kwargs)
        kw.update(more_kwargs)
        return self._fn(*self._args, *more_args, **kw)


def lru_cache(maxsize=None, typed=False):
    """Cache-forever skeleton. Decorator form accepts optional maxsize
    and typed flags for API compatibility; ignores them. Real LRU
    eviction is pending P6C.4 full stdlib work."""
    def _decorate(fn):
        cache: dict = {}

        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            if key in cache:
                return cache[key]
            v = fn(*args, **kwargs)
            cache[key] = v
            return v
        return wrapper
    # Support both @lru_cache and @lru_cache() / @lru_cache(maxsize=…).
    if callable(maxsize):
        fn = maxsize
        maxsize = None
        return _decorate(fn)
    return _decorate


def cache(fn):
    """Shorthand for ``@lru_cache(maxsize=None)``."""
    return lru_cache(maxsize=None)(fn)
