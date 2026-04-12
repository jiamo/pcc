"""pcc.py_stdlib.abc — narrow ``abc`` skeleton."""
from __future__ import annotations


def abstractmethod(fn):
    """Mark the method as abstract. pcc's class_gen treats
    ``@abstractmethod`` as a Phase-3 decorator that simply attaches a
    flag to the method, NotImplementedError-style."""
    fn.__isabstractmethod__ = True
    return fn


class ABC:
    """Abstract base class marker. pcc's class lowering reads the
    ``__abstractmethods__`` attribute (if present) to diagnose
    instantiation of incomplete concrete subclasses."""
    __slots__ = ()


class ABCMeta(type):
    pass
