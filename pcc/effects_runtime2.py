from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable


class UnhandledEffect(RuntimeError):
    pass


@dataclass(frozen=True)
class EffectRequest:
    name: str
    payload: Any = None


class Continuation:
    def __init__(self, resume: Callable[[Any], Any]) -> None:
        self._resume = resume
        self._used = False

    def resume(self, value: Any = None) -> Any:
        if self._used:
            raise RuntimeError("continuation is linear and was already resumed")
        self._used = True
        return self._resume(value)


_HANDLER_STACK: list[Callable[[EffectRequest, Continuation], Any]] = []


@contextmanager
def handle(handler: Callable[[EffectRequest, Continuation], Any]):
    _HANDLER_STACK.append(handler)
    try:
        yield
    finally:
        popped = _HANDLER_STACK.pop()
        assert popped is handler


def perform(name: str, payload: Any = None) -> Any:
    if not _HANDLER_STACK:
        raise UnhandledEffect(name)
    request = EffectRequest(name, payload)
    cont = Continuation(lambda value=None: value)
    return _HANDLER_STACK[-1](request, cont)
