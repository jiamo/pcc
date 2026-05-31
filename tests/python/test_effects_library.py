from __future__ import annotations

import pytest

from pcc.effects import Continuation, UnhandledEffect, handle, installed_handlers, perform


def test_perform_dispatches_to_nearest_handler():
    seen = []

    def outer(effect, k):
        seen.append(("outer", effect.payload))
        return k.resume("outer")

    def inner(effect, k):
        seen.append(("inner", effect.payload))
        return k.resume("inner")

    with handle({"ask": outer}):
        assert perform("ask", 1) == "outer"
        with handle({"ask": inner}):
            assert perform("ask", 2) == "inner"
    assert seen == [("outer", 1), ("inner", 2)]


def test_unhandled_effect_is_explicit():
    with pytest.raises(UnhandledEffect):
        perform("missing")


def test_continuation_is_linear():
    k = Continuation(lambda value=None: value)
    assert k.resume(3) == 3
    with pytest.raises(RuntimeError, match="already resumed"):
        k.resume(4)


def test_installed_handlers_reports_current_dynamic_scope():
    assert installed_handlers() == ()
    with handle({"a": lambda e, k: None, "b": lambda e, k: None}):
        assert installed_handlers() == ("a", "b")
