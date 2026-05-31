"""pcc.py_stdlib.functools — narrow skeleton for the self-host path.

``wraps``, ``lru_cache`` (LRU-free in this scaffold — just caches
all calls), ``reduce``, ``partial``. Full-strength LRU eviction +
typed cache key generation is P6C.4 work.
"""
from __future__ import annotations


def wraps(wrapped):
    """Minimal @wraps with CPython-visible metadata copying."""
    def _decorate(fn):
        fn.__name__ = getattr(wrapped, "__name__", fn.__name__)
        fn.__doc__ = getattr(wrapped, "__doc__", fn.__doc__)
        fn.__module__ = getattr(wrapped, "__module__", fn.__module__)
        fn.__wrapped__ = wrapped
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
        stats = {"hits": 0, "misses": 0}

        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            if key in cache:
                stats["hits"] += 1
                return cache[key]
            stats["misses"] += 1
            v = fn(*args, **kwargs)
            cache[key] = v
            return v

        def cache_info():
            return (stats["hits"], stats["misses"], maxsize, len(cache))

        def cache_clear():
            cache.clear()
            stats["hits"] = 0
            stats["misses"] = 0

        wrapper.cache_info = cache_info
        wrapper.cache_clear = cache_clear
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


class cached_property:
    def __init__(self, fn) -> None:
        self.fn = fn
        self.__name__ = getattr(fn, "__name__", "")
        self.__doc__ = getattr(fn, "__doc__", None)

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        value = self.fn(obj)
        setattr(obj, self.__name__, value)
        return value


def total_ordering(cls):
    if "__le__" not in cls.__dict__:
        def __le__(self, other):
            return self < other or self == other
        cls.__le__ = __le__
    if "__gt__" not in cls.__dict__:
        def __gt__(self, other):
            return not (self < other or self == other)
        cls.__gt__ = __gt__
    if "__ge__" not in cls.__dict__:
        def __ge__(self, other):
            return not (self < other)
        cls.__ge__ = __ge__
    return cls


def cmp_to_key(mycmp):
    class K:
        def __init__(self, obj) -> None:
            self.obj = obj

        def __lt__(self, other):
            return mycmp(self.obj, other.obj) < 0

        def __gt__(self, other):
            return mycmp(self.obj, other.obj) > 0

        def __eq__(self, other):
            return mycmp(self.obj, other.obj) == 0

        def __le__(self, other):
            return mycmp(self.obj, other.obj) <= 0

        def __ge__(self, other):
            return mycmp(self.obj, other.obj) >= 0

        def __ne__(self, other):
            return mycmp(self.obj, other.obj) != 0
    return K
