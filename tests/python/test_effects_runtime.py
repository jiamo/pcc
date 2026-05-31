from pcc.effects_runtime import handle, perform


def test_effect_handler_catches_signal():
    assert handle(
        lambda: perform("ask", 41),
        {"ask": lambda payload, k: payload + 1},
    ) == 42


def test_continuation_linear():
    saved = {}
    result = handle(
        lambda: perform("k"),
        {"k": lambda payload, k: saved.setdefault("k", k).resume("ok")},
    )
    assert result == "ok"
    try:
        saved["k"].resume("again")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
