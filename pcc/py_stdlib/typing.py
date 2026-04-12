"""pcc.py_stdlib.typing — runtime-noop reimplementation.

At runtime CPython's ``typing`` module is mostly a pile of generics
that evaluate to pass-through markers. pcc compiles annotations at
the type-inference layer; by the time we're at runtime they've
already been consumed. So the self-host replacement is a set of
trivial markers that return themselves under subscription.
"""
from __future__ import annotations


class _Marker:
    def __init__(self, name: str) -> None:
        self._name = name
    def __repr__(self) -> str:
        return self._name
    def __getitem__(self, key):
        return self
    def __call__(self, *a, **kw):
        return self


List      = _Marker("List")
Dict      = _Marker("Dict")
Set       = _Marker("Set")
Tuple     = _Marker("Tuple")
Optional  = _Marker("Optional")
Union     = _Marker("Union")
Callable  = _Marker("Callable")
Iterator  = _Marker("Iterator")
Iterable  = _Marker("Iterable")
Sequence  = _Marker("Sequence")
Any       = _Marker("Any")
ClassVar  = _Marker("ClassVar")
Final     = _Marker("Final")
Literal   = _Marker("Literal")
Generic   = _Marker("Generic")
Type      = _Marker("Type")
Mapping   = _Marker("Mapping")
NoReturn  = _Marker("NoReturn")


class TypeVar:
    def __init__(self, name: str, *bounds, **kwargs) -> None:
        self.name = name
    def __repr__(self) -> str:
        return f"~{self.name}"


def cast(t, v):
    """``typing.cast(T, value)`` is a no-op at runtime — returns value
    unchanged. pcc honors the declared type at the type-inference
    layer instead."""
    return v


def runtime_checkable(cls):
    return cls


def overload(fn):
    return fn
