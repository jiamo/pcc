"""pcc.py_stdlib.enum — minimal ``Enum`` / ``IntEnum`` skeleton.

Covers the form pcc uses: ``class Color(Enum): RED = 1; GREEN = 2``.
No auto() yet; no aliases; no _missing_ hook.
"""
from __future__ import annotations


class _EnumMeta(type):
    def __new__(mcs, name, bases, ns):
        cls = super().__new__(mcs, name, bases, ns)
        members: dict = {}
        for k, v in list(ns.items()):
            if k.startswith("_") or callable(v):
                continue
            inst = object.__new__(cls)
            inst._name_ = k
            inst._value_ = v
            setattr(cls, k, inst)
            members[k] = inst
        cls._member_map_ = members
        return cls


class Enum(metaclass=_EnumMeta):
    @property
    def name(self) -> str:
        return self._name_

    @property
    def value(self):
        return self._value_

    def __repr__(self) -> str:
        return f"<{type(self).__name__}.{self._name_}: {self._value_!r}>"


class IntEnum(int, Enum):
    pass


def auto():
    raise NotImplementedError("enum.auto() needs the counter machinery")
