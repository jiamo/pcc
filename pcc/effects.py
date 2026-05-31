"""Experimental algebraic-effect style library for pcc.

This is a library-level substrate, not syntax.  It gives the roadmap's effects
track a concrete API that normal Python can use today and that codegen can
recognize later for optimized delimited-continuation lowering.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import local as _thread_local
from typing import Any, Callable, Dict, Iterator, List, Mapping, MutableMapping


class UnhandledEffect(RuntimeError):
    pass


@dataclass(frozen=True)
class Effect:
    name: str
    payload: Any = None


class Continuation:
    """A linear continuation wrapper.

    The initial implementation is intentionally small: it enforces linear use
    and delegates to a Python callable. Future compiler support can replace the
    callable with a lowered stack/fiber continuation while preserving this API.
    """

    def __init__(self, resume_fn: Callable[[Any], Any]) -> None:
        self._resume_fn = resume_fn
        self._used = False

    @property
    def used(self) -> bool:
        return self._used

    def resume(self, value: Any = None) -> Any:
        if self._used:
            raise RuntimeError("continuation already resumed")
        self._used = True
        return self._resume_fn(value)


_tls = _thread_local()


def _stack() -> List[Mapping[str, Callable[[Effect, Continuation], Any]]]:
    stack = getattr(_tls, "handlers", None)
    if stack is None:
        stack = []
        _tls.handlers = stack
    return stack


@contextmanager
def handle(handlers: Mapping[str, Callable[[Effect, Continuation], Any]]) -> Iterator[None]:
    stack = _stack()
    stack.append(dict(handlers))
    try:
        yield
    finally:
        stack.pop()


def perform(name: str, payload: Any = None) -> Any:
    effect = Effect(name=name, payload=payload)
    for handlers in reversed(_stack()):
        handler = handlers.get(name)
        if handler is None:
            continue
        return handler(effect, Continuation(lambda value=None: value))
    raise UnhandledEffect(name)


def installed_handlers() -> tuple[str, ...]:
    names: list[str] = []
    for handlers in _stack():
        for name in handlers:
            if name not in names:
                names.append(name)
    return tuple(names)
