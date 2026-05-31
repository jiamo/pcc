from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class EffectSignal(Exception):
    def __init__(self, name: str, payload: Any) -> None:
        super().__init__(name)
        self.name = name
        self.payload = payload


@dataclass
class Continuation:
    _resume: Callable[[Any], Any]
    _used: bool = False

    def resume(self, value: Any = None) -> Any:
        if self._used:
            raise RuntimeError("continuation already resumed")
        self._used = True
        return self._resume(value)


def perform(name: str, payload: Any = None) -> Any:
    raise EffectSignal(name, payload)


def handle(fn: Callable[[], Any], handlers: dict[str, Callable[[Any, Continuation], Any]]) -> Any:
    try:
        return fn()
    except EffectSignal as signal:
        handler = handlers.get(signal.name)
        if handler is None:
            raise
        return handler(signal.payload, Continuation(lambda value=None: value))
