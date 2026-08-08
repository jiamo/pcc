"""Focused lifecycle gates for the PCC Harness plugin kernel."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects" / "harness"

import sys

if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from plugin_runtime import EventDispatchError, PluginKernel, PluginSpec


def test_dependency_graph_installs_in_stable_topological_order() -> None:
    kernel = PluginKernel()
    order: list[str] = []

    def installer(name: str):
        return lambda scope: order.append(name)

    installed = kernel.install_all(
        [
            PluginSpec("ui", installer("ui"), ["session", "tools"]),
            PluginSpec("tools", installer("tools"), ["core"]),
            PluginSpec("session", installer("session"), ["core"]),
            PluginSpec("core", installer("core")),
        ]
    )

    assert installed == ["core", "tools", "session", "ui"]
    assert order == installed
    assert kernel.active_plugins() == installed


def test_missing_and_cyclic_dependencies_reject_before_plugin_code() -> None:
    kernel = PluginKernel()
    calls: list[str] = []

    with pytest.raises(ValueError, match="missing"):
        kernel.install_all(
            [PluginSpec("ui", lambda scope: calls.append("ui"), ["session"])]
        )
    assert calls == []

    with pytest.raises(ValueError, match="cycle"):
        kernel.install_all(
            [
                PluginSpec("a", lambda scope: calls.append("a"), ["b"]),
                PluginSpec("b", lambda scope: calls.append("b"), ["a"]),
            ]
        )
    assert calls == []


def test_partial_batch_failure_unwinds_only_new_scopes_in_reverse_order() -> None:
    kernel = PluginKernel()
    events: list[str] = []
    kernel.install(
        "existing",
        lambda scope: scope.effect(lambda: lambda: events.append("existing")),
    )

    def install_a(scope) -> None:
        scope.effect(lambda: lambda: events.append("a-first"))
        scope.effect(lambda: lambda: events.append("a-last"))

    def install_b(scope) -> None:
        scope.effect(lambda: lambda: events.append("b"))
        raise RuntimeError("load failed")

    with pytest.raises(RuntimeError, match="load failed"):
        kernel.install_all(
            [
                PluginSpec("a", install_a, ["existing"]),
                PluginSpec("b", install_b, ["a"]),
            ]
        )

    assert events == ["b", "a-last", "a-first"]
    assert kernel.active_plugins() == ["existing"]


def test_unload_protects_dependencies_and_reload_has_clean_lifetime() -> None:
    kernel = PluginKernel()
    events: list[str] = []
    kernel.install(
        "core", lambda scope: scope.effect(lambda: lambda: events.append("core-off"))
    )
    kernel.install(
        "leaf",
        lambda scope: scope.effect(lambda: lambda: events.append("leaf-v1-off")),
        ["core"],
    )

    with pytest.raises(RuntimeError, match="required by"):
        kernel.unload("core")

    kernel.reload(
        "leaf", lambda scope: scope.effect(lambda: lambda: events.append("leaf-v2-off"))
    )
    assert events == ["leaf-v1-off"]
    assert kernel.active_plugins() == ["core", "leaf"]

    kernel.dispose()
    kernel.dispose()
    assert events == ["leaf-v1-off", "leaf-v2-off", "core-off"]


def test_failed_reload_releases_partial_native_resource() -> None:
    kernel = PluginKernel()
    released: list[str] = []
    kernel.install(
        "leaf", lambda scope: scope.effect(lambda: lambda: released.append("old"))
    )

    def broken(scope) -> None:
        scope.effect(lambda: lambda: released.append("partial"))
        raise RuntimeError("replacement failed")

    with pytest.raises(RuntimeError, match="replacement failed"):
        kernel.reload("leaf", broken)

    assert released == ["old", "partial"]
    assert kernel.active_plugins() == []


def test_consumer_waits_then_reloads_when_provider_identity_changes() -> None:
    kernel = PluginKernel()
    events: list[str] = []

    def consumer(scope) -> None:
        service = scope.require("fs", "editor")
        events.append("editor-on:" + service)

        def cleanup() -> None:
            events.append("editor-off:" + scope.get("fs"))

        scope.effect(lambda: cleanup)

    kernel.install("editor", consumer, inject=["fs"])
    assert kernel.plugin_state("editor") == "PENDING"
    assert kernel.active_plugins() == []

    def provider_v1(scope) -> None:
        scope.effect(lambda: lambda: events.append("provider-v1-resource-off"))
        scope.provide("fs", "v1")

    kernel.install("fs-provider", provider_v1, provides=["fs"])
    assert kernel.plugin_state("editor") == "ACTIVE"
    assert events == ["editor-on:v1"]

    kernel.unload("fs-provider")
    assert kernel.plugin_state("editor") == "PENDING"
    assert events == [
        "editor-on:v1",
        "editor-off:v1",
        "provider-v1-resource-off",
    ]

    kernel.install(
        "fs-provider",
        lambda scope: scope.provide("fs", "v2"),
        provides=["fs"],
    )
    assert kernel.plugin_state("editor") == "ACTIVE"
    assert events[-1] == "editor-on:v2"


def test_isolated_realms_activate_only_matching_consumers() -> None:
    kernel = PluginKernel()
    first = kernel.context.isolate("model", "session-1")
    second = kernel.context.isolate("model", "session-2")
    seen: list[str] = []

    kernel.install(
        "first-consumer",
        lambda scope: seen.append("first:" + scope.get("model")),
        inject=["model"],
        parent=first,
    )
    kernel.install(
        "second-consumer",
        lambda scope: seen.append("second:" + scope.get("model")),
        inject=["model"],
        parent=second,
    )
    kernel.install(
        "first-provider",
        lambda scope: scope.provide("model", "m1"),
        provides=["model"],
        parent=first,
    )

    assert seen == ["first:m1"]
    assert kernel.plugin_state("first-consumer") == "ACTIVE"
    assert kernel.plugin_state("second-consumer") == "PENDING"
    diagnostic = kernel.dependency_diagnostics()[0]
    assert "second-consumer" in diagnostic
    assert "session-2" in diagnostic


def test_event_modes_order_short_circuit_snapshot_and_aggregate_failures() -> None:
    kernel = PluginKernel()
    scope = kernel.context.child("events")
    seen: list[str] = []

    scope.on("notice", lambda value: seen.append("normal:" + value))
    scope.on("notice", lambda value: seen.append("first:" + value), prepend=True)
    scope.once("notice", lambda value: seen.append("once:" + value))
    scope.emit("notice", "a")
    scope.emit("notice", "b")
    assert seen == ["first:a", "normal:a", "once:a", "first:b", "normal:b"]

    scope.on("decision", lambda value: None, "bail")
    scope.on("decision", lambda value: "accepted:" + value, "bail")
    scope.on("decision", lambda value: "late", "bail")
    assert scope.bail("decision", "x") == "accepted:x"

    scope.on("serial-decision", lambda value: False, "serial")
    scope.on("serial-decision", lambda value: "serial:" + value, "serial")
    assert scope.serial("serial-decision", "x") == "serial:x"

    def fail_one(value) -> None:
        seen.append("parallel-1")
        raise RuntimeError("one")

    def fail_two(value) -> None:
        seen.append("parallel-2")
        raise RuntimeError("two")

    scope.on("checkpoint", fail_one, "parallel")
    scope.on("checkpoint", fail_two, "parallel")
    with pytest.raises(EventDispatchError) as captured:
        scope.parallel("checkpoint", "x")
    assert len(captured.value.failures) == 2
    assert seen[-2:] == ["parallel-1", "parallel-2"]


def test_pending_dependency_diagnostic_and_declared_provision_failure() -> None:
    kernel = PluginKernel()
    calls: list[str] = []
    kernel.install("waiting", lambda scope: calls.append("loaded"), inject=["tools"])
    assert calls == []
    with pytest.raises(RuntimeError, match="waiting.*tools.*root:tools"):
        kernel.assert_healthy()

    with pytest.raises(RuntimeError, match="did not provide"):
        kernel.install("false-provider", lambda scope: None, provides=["tools"])
    assert kernel.plugin_state("waiting") == "PENDING"


def test_graph_snapshot_exposes_realms_provider_identity_and_effect_owners() -> None:
    kernel = PluginKernel()
    session = kernel.context.isolate("model", "session-a")

    kernel.install(
        "consumer",
        lambda scope: scope.require("model", "consumer"),
        inject=["model"],
        parent=session,
    )
    pending = kernel.graph_snapshot()
    assert pending["plugins"] == [
        {
            "name": "consumer",
            "state": "PENDING",
            "scope": "harness/isolate:model/consumer",
            "requires": [],
            "inject": [
                {
                    "service": "model",
                    "realm": "session-a",
                    "provider": "",
                    "providerId": 0,
                    "relationship": "",
                    "committed": False,
                }
            ],
            "provides": [],
            "lastError": "",
        }
    ]

    def provider(scope) -> None:
        scope.effect(lambda: lambda: None, "model-resource")
        scope.provide("model", "deepseek", "session-a-model")

    kernel.install(
        "provider",
        provider,
        provides=["model"],
        parent=session,
    )
    active = kernel.graph_snapshot()
    consumer = active["plugins"][0]
    assert consumer["state"] == "ACTIVE"
    assert consumer["inject"][0]["provider"] == "provider"
    assert consumer["inject"][0]["providerId"] > 0
    assert consumer["inject"][0]["relationship"] == "session-a-model"
    assert consumer["inject"][0]["committed"] is True
    assert active["services"] == [
        {
            "name": "model",
            "realm": "session-a",
            "provider": "provider",
            "providerId": consumer["inject"][0]["providerId"],
            "relationship": "session-a-model",
            "published": True,
        }
    ]
    assert {
        "scope": "harness/isolate:model/provider",
        "kind": "effect",
        "label": "model-resource",
    } in active["effects"]
    assert {
        "scope": "harness/isolate:model/provider",
        "kind": "service",
        "label": "provide:model",
    } in active["effects"]

    kernel.unload("provider")
    withdrawn = kernel.graph_snapshot()
    assert withdrawn["plugins"][0]["state"] == "PENDING"
    assert withdrawn["plugins"][0]["inject"][0]["provider"] == ""
    assert withdrawn["services"] == []


@pytest.mark.integration
def test_current_pcc1_plugin_lifecycle() -> None:
    binary = PROJECT / "build" / "harness-core"
    completed = subprocess.run(
        [str(binary), "--self-check"],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "HARNESS_RUNTIME_SELF_CHECK_OK" in completed.stdout
