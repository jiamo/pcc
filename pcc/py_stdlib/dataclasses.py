"""pcc.py_stdlib.dataclasses — scaffold for the ``@dataclass`` macro.

Real self-host note: pcc's class_gen is expected to recognize
``@dataclass`` at compile time and synthesize ``__init__`` /
``__repr__`` / ``__eq__`` directly in the class body, in which case
this runtime helper is never called. The interpreted fallback here
mirrors the synthesized behavior for tests that exercise dataclasses
under CPython before pcc's class_gen picks them up.
"""
from __future__ import annotations


_host_dataclasses = __import__("dataclasses")

MISSING = _host_dataclasses.MISSING


def field(default=MISSING, default_factory=MISSING, init=True, repr=True,
          compare=True, kw_only=False):
    kwargs = {
        "init": init,
        "repr": repr,
        "compare": compare,
        "kw_only": kw_only,
    }
    if default is not MISSING:
        kwargs["default"] = default
    if default_factory is not MISSING:
        kwargs["default_factory"] = default_factory
    return _host_dataclasses.field(**kwargs)


class _DataclassFactory:
    def __init__(self, init=True, repr=True, eq=True, frozen=False,
                 kw_only=False, slots=False, order=False,
                 unsafe_hash=False):
        self.init = init
        self.repr = repr
        self.eq = eq
        self.frozen = frozen
        self.kw_only = kw_only
        self.slots = slots
        self.order = order
        self.unsafe_hash = unsafe_hash

    def __call__(self, cls):
        return dataclass(
            cls,
            init=self.init,
            repr=self.repr,
            eq=self.eq,
            frozen=self.frozen,
            kw_only=self.kw_only,
            slots=self.slots,
            order=self.order,
            unsafe_hash=self.unsafe_hash,
        )


def dataclass(cls=None, init=True, repr=True, eq=True, frozen=False,
              kw_only=False, slots=False, order=False, unsafe_hash=False):
    """``@dataclass`` — synthesize ``__init__``, ``__repr__``, ``__eq__``
    from the class-level annotations."""
    return _host_dataclasses.dataclass(
        cls,
        init=init,
        repr=repr,
        eq=eq,
        frozen=frozen,
        kw_only=kw_only,
        slots=slots,
        order=order,
        unsafe_hash=unsafe_hash,
    )


def fields(class_or_instance):
    return _host_dataclasses.fields(class_or_instance)


def is_dataclass(obj):
    return _host_dataclasses.is_dataclass(obj)


def asdict(obj):
    return _host_dataclasses.asdict(obj)


def astuple(obj):
    return _host_dataclasses.astuple(obj)


def replace(obj, **changes):
    return _host_dataclasses.replace(obj, **changes)


def make_dataclass(cls_name: str, fields, **kwargs):
    return _host_dataclasses.make_dataclass(cls_name, fields, **kwargs)
