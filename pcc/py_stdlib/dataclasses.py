"""pcc.py_stdlib.dataclasses — scaffold for the ``@dataclass`` macro.

Real self-host note: pcc's class_gen is expected to recognize
``@dataclass`` at compile time and synthesize ``__init__`` /
``__repr__`` / ``__eq__`` directly in the class body, in which case
this runtime helper is never called. The interpreted fallback here
mirrors the synthesized behavior for tests that exercise dataclasses
under CPython before pcc's class_gen picks them up.
"""
from __future__ import annotations


MISSING = object()


def field(default=MISSING, default_factory=MISSING, init=True, repr=True,
          compare=True, kw_only=False):
    class _Field:
        pass
    f = _Field()
    f.default = default
    f.default_factory = default_factory
    f.init = init
    f.repr = repr
    f.compare = compare
    f.kw_only = kw_only
    return f


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
    if cls is None:
        return _DataclassFactory(
            init=init,
            repr=repr,
            eq=eq,
            frozen=frozen,
            kw_only=kw_only,
            slots=slots,
            order=order,
            unsafe_hash=unsafe_hash,
        )

    ann = getattr(cls, "__annotations__", {})
    field_names = list(ann.keys())

    if init and "__init__" not in cls.__dict__:
        params = ", ".join(field_names)
        body = "\n".join(
            f"    self.{n} = {n}" for n in field_names
        ) or "    pass"
        src = f"def __init__(self, {params}):\n{body}\n"
        ns: dict = {}
        exec(src, ns)
        cls.__init__ = ns["__init__"]

    if repr and "__repr__" not in cls.__dict__:
        def __repr__(self, _field_names=field_names):
            parts = ", ".join(
                f"{n}={getattr(self, n)!r}" for n in _field_names
            )
            return f"{type(self).__name__}({parts})"
        cls.__repr__ = __repr__

    if eq and "__eq__" not in cls.__dict__:
        def __eq__(self, other, _field_names=field_names):
            if type(other) is not type(self):
                return NotImplemented
            for n in _field_names:
                if getattr(self, n) != getattr(other, n):
                    return False
            return True
        cls.__eq__ = __eq__

    return cls


def fields(class_or_instance):
    ann = getattr(class_or_instance, "__annotations__", {})
    out = []
    for name, ty in ann.items():
        f = field()
        f.name = name
        f.type = ty
        out.append(f)
    return out


def is_dataclass(obj):
    return hasattr(obj, "__dataclass_fields__") or hasattr(
        type(obj), "__dataclass_fields__",
    )


def asdict(obj):
    ann = getattr(type(obj), "__annotations__", {})
    return {n: getattr(obj, n) for n in ann}
