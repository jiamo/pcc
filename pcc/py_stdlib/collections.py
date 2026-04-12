"""pcc.py_stdlib.collections — skeleton for pcc's self-host path.

Only the types pcc imports: ``OrderedDict``, ``defaultdict``,
``Counter``, ``deque``, ``namedtuple``. Implementations lean on
dict/list primitives pcc already ships natively.
"""
from __future__ import annotations


class OrderedDict(dict):
    """pcc's native dict is already insertion-ordered (phase 2
    contract), so ``OrderedDict`` is a subtype with the same storage
    plus the few extra methods real OrderedDict has."""

    def move_to_end(self, key, last: bool = True) -> None:
        v = self[key]
        del self[key]
        if last:
            self[key] = v
        else:
            # Python OrderedDict move_to_end(last=False) puts at the
            # front. Recreate by popping everything and reinserting.
            saved = list(self.items())
            self.clear()
            self[key] = v
            for k, v2 in saved:
                self[k] = v2

    def popitem(self, last: bool = True):
        if not self:
            raise KeyError("dictionary is empty")
        if last:
            k = next(reversed(self))
        else:
            k = next(iter(self))
        v = self[k]
        del self[k]
        return k, v


class defaultdict(dict):
    def __init__(self, default_factory=None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.default_factory = default_factory

    def __missing__(self, key):
        if self.default_factory is None:
            raise KeyError(key)
        v = self.default_factory()
        self[key] = v
        return v


class Counter(dict):
    def __init__(self, iterable=None) -> None:
        super().__init__()
        if iterable is not None:
            for item in iterable:
                self[item] = self.get(item, 0) + 1

    def most_common(self, n=None):
        items = sorted(self.items(), key=lambda kv: -kv[1])
        if n is None:
            return items
        return items[:n]


class deque:
    def __init__(self, iterable=None, maxlen=None) -> None:
        self._data = [] if iterable is None else list(iterable)
        self.maxlen = maxlen

    def append(self, v) -> None:
        self._data.append(v)
        if self.maxlen is not None and len(self._data) > self.maxlen:
            self._data.pop(0)

    def appendleft(self, v) -> None:
        self._data.insert(0, v)
        if self.maxlen is not None and len(self._data) > self.maxlen:
            self._data.pop()

    def pop(self):
        return self._data.pop()

    def popleft(self):
        return self._data.pop(0)

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def namedtuple(name, field_spec):
    """Minimal ``namedtuple`` — supports space-or-comma separated
    field specs; returns a class whose instances are tuples with
    named attribute access."""
    if isinstance(field_spec, str):
        fields = tuple(
            f.strip() for f in field_spec.replace(",", " ").split() if f.strip()
        )
    else:
        fields = tuple(field_spec)

    class _NT(tuple):
        _fields = fields

        def __new__(cls, *args, **kwargs):
            vals = list(args)
            for i, f in enumerate(fields):
                if i < len(vals):
                    continue
                if f in kwargs:
                    vals.append(kwargs[f])
                else:
                    raise TypeError(f"missing field {f!r}")
            return tuple.__new__(cls, vals)

        def __repr__(self):
            parts = ", ".join(
                f"{f}={v!r}" for f, v in zip(fields, self)
            )
            return f"{name}({parts})"

        def _asdict(self):
            return {f: v for f, v in zip(fields, self)}

    _NT.__name__ = name
    for i, f in enumerate(fields):
        setattr(_NT, f, property(lambda self, i=i: self[i]))
    return _NT
