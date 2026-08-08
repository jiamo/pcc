import pytest

from plugin_runtime import PluginKernel


def test_scoped_services_override_and_dispose_to_parent():
    kernel = PluginKernel()
    kernel.context.provide("model", "global")
    child = kernel.context.isolate("model")
    registration = child.provide("model", "scoped", "agent-1")

    assert child.get("model") == "scoped"
    assert child.service_relationship("model") == "agent-1"
    assert kernel.context.get("model") == "global"

    registration.dispose()
    assert not child.has("model")
    assert kernel.context.get("model") == "global"
    assert registration.active is False


def test_events_are_visible_to_registration_scope_descendants_only():
    kernel = PluginKernel()
    left = kernel.context.child("left")
    right = kernel.context.child("right")
    seen = []
    kernel.context.on("change", lambda value: seen.append("root:" + value))
    left.on("change", lambda value: seen.append("left:" + value))

    left.emit("change", "a")
    right.emit("change", "b")

    assert seen == ["root:a", "left:a", "root:b"]


def test_disposal_is_reverse_order_and_idempotent():
    kernel = PluginKernel()
    scope = kernel.context.child("plugin")
    disposed = []
    scope.effect(lambda: lambda: disposed.append("first"))
    scope.effect(lambda: lambda: disposed.append("second"))

    scope.dispose()
    scope.dispose()

    assert disposed == ["second", "first"]
    with pytest.raises(RuntimeError, match="disposed"):
        scope.get("anything")


def test_waterfall_requires_explicit_delegation():
    kernel = PluginKernel()
    kernel.context.on(
        "policy", lambda value, next_: next_(value + "-a") + "-after", "waterfall"
    )
    kernel.context.on("policy", lambda value, next_: "blocked:" + value, "waterfall")

    result = kernel.context.waterfall("policy", "start", lambda value: value + "-body")

    assert result == "blocked:start-a-after"


def test_install_rolls_back_effects_when_plugin_raises():
    kernel = PluginKernel()
    disposed = []

    def broken(scope):
        scope.effect(lambda: lambda: disposed.append("rolled-back"))
        scope.provide("partial", object())
        raise RuntimeError("load failed")

    with pytest.raises(RuntimeError, match="load failed"):
        kernel.install("broken", broken)

    assert disposed == ["rolled-back"]
    assert not kernel.context.has("partial")


def test_joined_service_realms_share_provider_without_leaking_to_root():
    kernel = PluginKernel()
    left = kernel.context.isolate("fs", "workspace-a")
    right = kernel.context.isolate("fs", "workspace-a")
    other = kernel.context.isolate("fs", "workspace-b")
    left.provide("fs", "private")

    assert right.get("fs") == "private"
    assert not other.has("fs")
    assert not kernel.context.has("fs")


def test_effect_setup_runs_now_and_cleanup_continues_after_failure():
    kernel = PluginKernel()
    scope = kernel.context.child("owned")
    events = []

    def setup():
        events.append("setup")

        def fail():
            events.append("fail")
            raise RuntimeError("cleanup failed")

        return [lambda: events.append("first"), fail]

    scope.effect(setup)
    scope.effect(lambda: lambda: events.append("last"))
    assert events == ["setup"]

    with pytest.raises(RuntimeError, match="cleanup failed"):
        scope.dispose()
    assert events == ["setup", "last", "fail", "first"]


def test_generator_effect_collects_and_rolls_back_yielded_disposers():
    kernel = PluginKernel()
    scope = kernel.context.child("generator-effect")
    events = []

    def setup():
        events.append("setup")
        yield lambda: events.append("first-off")
        yield lambda: events.append("second-off")
        raise RuntimeError("generator setup failed")

    with pytest.raises(RuntimeError, match="generator setup failed"):
        scope.effect(setup)

    assert events == ["setup", "second-off", "first-off"]
    assert scope.effects == []


def test_invalid_generator_effect_value_rolls_back_prior_disposers():
    kernel = PluginKernel()
    scope = kernel.context.child("invalid-generator-effect")
    events = []

    def setup():
        yield lambda: events.append("released")
        yield "not-a-disposer"

    with pytest.raises(TypeError, match="invalid effect disposer"):
        scope.effect(setup)

    assert events == ["released"]
    assert scope.effects == []


def test_reentrant_owner_disposal_observes_effect_during_setup():
    kernel = PluginKernel()
    scope = kernel.context.child("reentrant")
    events = []

    def setup():
        events.append("setup-start")
        scope.dispose()
        events.append("setup-finish")
        return lambda: events.append("cleanup")

    effect = scope.effect(setup)

    assert events == ["setup-start", "setup-finish", "cleanup"]
    assert effect.active is False
    assert scope.active is False


def test_cleanup_cannot_register_a_new_effect_on_unloading_owner():
    kernel = PluginKernel()
    scope = kernel.context.child("unloading")
    errors = []

    def cleanup():
        try:
            scope.effect(lambda: lambda: None)
        except RuntimeError as error:
            errors.append(str(error))

    scope.effect(lambda: cleanup)
    scope.dispose()

    assert len(errors) == 1
    assert "disposed" in errors[0]
    assert len(scope.effects) == 1
