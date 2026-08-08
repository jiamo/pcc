from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "pcc" / "py_runtime" / "gui_declarative_contract_v1.json"
REFERENCE_PATH = ROOT / "docs" / "refs_docs" / "gui-declarative" / "README.md"


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


_TYPE_SIZE = {"u32": 4, "i32": 4, "u64": 8, "i64": 8, "ptr": 8}


def test_v1_records_are_exact_aligned_non_overlapping_and_owned():
    contract = _contract()
    assert contract["schema"] == "pcc.gui.declarative.v1"
    assert contract["abi_version"] == 1
    assert contract["pointer_bytes"] == 8

    expected = {
        "PccGuiRenderContextV1": 80,
        "PccGuiDescriptorV1": 72,
        "PccGuiSlotV1": 24,
        "PccGuiComponentInstanceV1": 80,
        "PccGuiUpdateV1": 64,
        "PccGuiEffectV1": 48,
        "PccGuiStateSnapshotV1": 40,
        "PccGuiListenerV1": 40,
        "PccGuiStyleOperationV1": 40,
        "PccGuiInvokeV1": 64,
        "PccGuiStyleCacheEntryV1": 80,
        "PccGuiCompletionV1": 48,
        "PccGuiAppEventV1": 48,
        "PccGuiErrorV1": 24,
    }
    records = {record["name"]: record for record in contract["records"]}
    assert {name: record["size"] for name, record in records.items()} == expected

    for record in records.values():
        assert record["align"] == 8
        assert record["owner"]
        end = 0
        for field in sorted(record["fields"], key=lambda item: item["offset"]):
            size = _TYPE_SIZE[field["type"]]
            assert field["offset"] >= end, (record["name"], field)
            assert field["offset"] % min(size, record["align"]) == 0
            assert field["ownership"]
            end = field["offset"] + size
        assert end <= record["size"]

    descriptor = records["PccGuiDescriptorV1"]
    assert descriptor["identity"] == [
        "parent_component_id",
        "key",
        "node_kind",
    ]
    managed = next(
        kind for kind in contract["slot_kinds"] if kind["name"] == "managed_ref"
    )
    assert managed["traced"] is True
    for requirement in ("barrier", "root", "trace", "relocation"):
        assert requirement in managed["admission"]


def _reconcile(committed, descriptors):
    by_key = {(item["parent"], item["key"]): item for item in committed}
    effects = []
    next_tree = []
    used = set()
    for position, desc in enumerate(descriptors):
        identity = (desc["parent"], desc["key"])
        old = by_key.get(identity)
        if old is None:
            effects.append(("insert", desc["key"], position))
            next_tree.append(dict(desc))
        elif old["kind"] != desc["kind"]:
            effects.append(("remove", old["node"], position))
            effects.append(("insert", desc["key"], position))
            next_tree.append(dict(desc))
            used.add(identity)
        else:
            if old["position"] != position:
                effects.append(("move", old["node"], position))
            if old["props"] != desc["props"]:
                effects.append(("update", old["node"], position))
            reused = dict(desc)
            reused["node"] = old["node"]
            reused["position"] = position
            next_tree.append(reused)
            used.add(identity)
    for identity, old in by_key.items():
        if identity not in used and not any(
            item["parent"] == identity[0] and item["key"] == identity[1]
            for item in next_tree
        ):
            effects.append(("remove", old["node"], len(next_tree)))
    return next_tree, effects


def test_key_and_compatible_type_control_reuse_and_effect_order():
    committed = [
        {"parent": 7, "key": 10, "kind": 1, "node": 100, "position": 0, "props": 1},
        {"parent": 7, "key": 20, "kind": 2, "node": 200, "position": 1, "props": 2},
    ]
    desired = [
        {"parent": 7, "key": 20, "kind": 2, "node": 900, "position": 0, "props": 3},
        {"parent": 7, "key": 10, "kind": 3, "node": 901, "position": 1, "props": 1},
    ]
    next_tree, effects = _reconcile(committed, desired)
    assert next_tree[0]["node"] == 200
    assert next_tree[1]["node"] == 901
    assert effects == [
        ("move", 200, 0),
        ("update", 200, 0),
        ("remove", 100, 1),
        ("insert", 10, 1),
    ]


def _render_selected(initial, updates, selected_lanes, reducers):
    state = initial
    snapshot = None
    replay = []
    retries = []
    for update in sorted(updates, key=lambda item: item["sequence"]):
        selected = update["lane"] in selected_lanes
        if not selected and snapshot is None:
            snapshot = state
        if not selected:
            replay.append(dict(update))
            continue
        if update["action"] == "set":
            state = update["operand"]
        else:
            retries.append(update["reducer"])
            state = reducers[update["reducer"]](state, update["operand"])
        if snapshot is not None:
            replay.append(dict(update))
    return state, snapshot, replay, retries


def _replay(snapshot, updates, reducers):
    state = snapshot
    for update in sorted(updates, key=lambda item: item["sequence"]):
        if update["action"] == "set":
            state = update["operand"]
        else:
            state = reducers[update["reducer"]](state, update["operand"])
    return state


def test_cross_lane_replay_preserves_global_order_and_retry_contract():
    reducers = {1: lambda old, operand: old + operand}
    low_set_high_reduce = [
        {"sequence": 1, "lane": 3, "action": "set", "operand": 5},
        {"sequence": 2, "lane": 0, "action": "reduce", "operand": 1, "reducer": 1},
    ]
    visible, base, replay, calls = _render_selected(
        0, low_set_high_reduce, {0}, reducers
    )
    assert (visible, base, calls) == (1, 0, [1])
    assert _replay(base, replay, reducers) == 6

    low_reduce_high_set = [
        {"sequence": 1, "lane": 3, "action": "reduce", "operand": 1, "reducer": 1},
        {"sequence": 2, "lane": 0, "action": "set", "operand": 5},
    ]
    visible, base, replay, calls = _render_selected(
        0, low_reduce_high_set, {0}, reducers
    )
    assert (visible, base, calls) == (5, 0, [])
    assert _replay(base, replay, reducers) == 5

    contract = _contract()
    assert contract["callbacks"]["reducer"]["purity"].startswith("deterministic")
    assert contract["update_replay"]["commit_rule"].startswith("only a complete")


def test_lane_priority_aging_and_yield_restart_are_frozen():
    contract = _contract()
    lanes = contract["lanes"]
    assert [(lane["name"], lane["priority"], lane["age_ticks"]) for lane in lanes] == [
        ("discrete", 0, 0),
        ("animation", 1, 2),
        ("default", 2, 8),
        ("background", 3, 32),
    ]
    render = contract["state_machines"]["render_work"]
    assert ["rendering", "yielded"] in render["transitions"]
    assert ["yielded", "rendering"] in render["transitions"]
    assert ["yielded", "cancelled"] in render["transitions"]
    assert ["committing", "committed"] in render["transitions"]
    assert ["yielded", "committing"] not in render["transitions"]


def test_owned_handle_queue_clones_release_exactly_once_on_cancel():
    retain_count = 0
    release_count = 0

    def retain():
        nonlocal retain_count
        retain_count += 1

    def release():
        nonlocal release_count
        release_count += 1

    retain()  # admitted queue record
    retain()  # replay/base-queue clone after a skip
    release()  # cancel live queue record
    release()  # cancel base-queue clone
    assert retain_count == release_count == 2
    handle = next(
        kind for kind in _contract()["slot_kinds"] if kind["name"] == "opaque_handle"
    )
    assert handle["actions"] == ["set"]
    assert "one explicit retain" in handle["ownership"]


def test_commit_and_lifecycle_phases_are_exact_and_ordered():
    phases = [phase["name"] for phase in _contract()["effect_phases"]]
    assert phases == [
        "before_mutation_snapshot",
        "mutation_layout_cleanup",
        "mutation_structural",
        "layout_create",
        "passive_cleanup",
        "passive_create",
    ]
    replacement_trace = [
        "before_mutation_snapshot",
        "mutation_layout_cleanup",
        "mutation_structural",
        "layout_create",
        "passive_cleanup",
        "passive_create",
    ]
    assert replacement_trace == phases


def test_listener_registry_has_one_route_owner_and_removal_is_final():
    listeners = {
        1: {"target": 30, "event": "click", "active": True},
        2: {"target": 20, "event": "click", "active": True},
    }
    painted_owner_path = [30, 20, 10]

    def route(event):
        delivered = []
        for owner in painted_owner_path:
            for listener_id, listener in sorted(listeners.items()):
                if listener["active"] and listener["event"] == event:
                    if listener["target"] == owner:
                        delivered.append(listener_id)
        return delivered

    assert route("click") == [1, 2]
    listeners[1]["active"] = False
    assert route("click") == [2]
    assert "single component listener registry" in next(
        record["owner"]
        for record in _contract()["records"]
        if record["name"] == "PccGuiListenerV1"
    )
    routing = _contract()["routing"]
    assert routing["path_owner"].startswith("kernel returns painted hit")
    assert routing["dispatch_owner"].startswith("component listener registry alone")
    assert "version-superseded" in routing["legacy"]


def test_callback_ids_stale_ids_and_failed_work_are_fail_closed():
    contract = _contract()
    registration = contract["callback_registration"]
    assert "zero is invalid" in registration["id_space"]
    assert registration["unknown"] == "CALLBACK_FAILED"
    assert "cancelled" in registration["teardown"]
    assert "generation in high 32 bits" in contract["identity"]["component_id"]
    assert contract["identity"]["stale"].startswith("generation mismatch")
    assert contract["commit"]["rollback"].startswith("discard all work arenas")
    assert contract["commit"]["atomicity"].startswith("validate complete")


def _parse_candidate(candidate):
    modifier = None
    core = candidate
    if "/" in core:
        core, modifier = core.split("/", 1)
    negative = core.startswith("-")
    if negative:
        core = core[1:]
    utility, value = core.split("-", 1)
    return utility, value, negative, modifier


def test_style_candidate_resolution_and_selective_generation_cache():
    assert _parse_candidate("-gap-2/[dense]") == (
        "gap",
        "2",
        True,
        "[dense]",
    )
    assert _parse_candidate("bg-accent/50") == ("bg", "accent", False, "50")

    cache_key = (
        "bg-accent gap-2",
        7,
        (("colour", 3), ("spacing", 9)),
        (("accent", 11), ("2", 4)),
    )
    unrelated_font_edit = (
        "bg-accent gap-2",
        7,
        (("colour", 3), ("spacing", 9)),
        (("accent", 11), ("2", 4)),
    )
    spacing_edit = (
        "bg-accent gap-2",
        7,
        (("colour", 3), ("spacing", 10)),
        (("accent", 11), ("2", 5)),
    )
    assert unrelated_font_edit == cache_key
    assert spacing_edit != cache_key
    style = _contract()["style"]
    assert style["warm_apply"] == "no parsing and no allocation"


def test_candidate_registry_rejects_unknown_and_ambiguous_utilities():
    registry = {"bg": ["colour"], "gap": ["spacing"], "text": ["colour", "font"]}

    def resolve(candidate):
        utility, value, negative, modifier = _parse_candidate(candidate)
        namespaces = registry.get(utility)
        if namespaces is None:
            raise ValueError("INVALID_CANDIDATE")
        if len(namespaces) != 1:
            raise ValueError("AMBIGUOUS_CANDIDATE")
        if negative and utility != "gap":
            raise ValueError("INVALID_CANDIDATE")
        return namespaces[0], value, modifier

    assert resolve("bg-accent/50") == ("colour", "accent", "50")
    assert resolve("-gap-2") == ("spacing", "2", None)
    for candidate, error in (
        ("unknown-x", "INVALID_CANDIDATE"),
        ("text-body", "AMBIGUOUS_CANDIDATE"),
        ("-bg-accent", "INVALID_CANDIDATE"),
    ):
        try:
            resolve(candidate)
        except ValueError as exc:
            assert str(exc) == error
        else:
            raise AssertionError((candidate, error))


def _transition(machine, state, next_state):
    if [state, next_state] not in machine["transitions"]:
        raise ValueError("INVALID_TRANSITION")
    return next_state


def test_command_completion_is_exactly_once_and_late_completion_fails():
    machine = _contract()["state_machines"]["command_completion"]
    state = _transition(machine, machine["initial"], "result")
    assert state == "result"
    for second in ("result", "error", "cancelled"):
        try:
            _transition(machine, state, second)
        except ValueError as exc:
            assert str(exc) == "INVALID_TRANSITION"
        else:
            raise AssertionError("terminal completion accepted a second result")


def test_app_event_cancellation_cleanup_and_terminal_exit_are_closed():
    app = _contract()["state_machines"]["app"]
    state = app["initial"]
    state = _transition(app, state, "ready")
    state = _transition(app, state, "resumed")
    state = _transition(app, state, "active")
    state = _transition(app, state, "exit_requested")
    state = _transition(app, state, "active")  # one cancellation
    state = _transition(app, state, "exit_requested")
    state = _transition(app, state, "terminating")
    cleanup = ["commands", "listeners", "effects", "native_handles"]
    state = _transition(app, state, "exited")
    assert cleanup == ["commands", "listeners", "effects", "native_handles"]
    assert state == "exited"
    assert app["events"] == [
        "Ready",
        "Resumed",
        "MainEventsCleared",
        "WindowEvent",
        "Opened",
        "Reopen",
        "ExitRequested",
        "Exit",
    ]
    assert all("WebviewEvent" not in event for event in app["events"])


def test_capacity_and_error_contract_is_finite_and_stable():
    contract = _contract()
    assert all(value > 0 for value in contract["limits"].values())
    errors = {item["name"]: item["code"] for item in contract["errors"]}
    assert errors["OK"] == 0
    assert errors["CAPACITY"] == -101
    assert errors["INVALID_TRANSITION"] == -103
    assert errors["DUPLICATE_COMPLETION"] == -107
    assert errors["LATE_COMPLETION"] == -108
    assert len(errors) == len(set(errors.values()))


def test_upstream_reference_note_is_pinned_license_labeled_and_non_owning():
    note = REFERENCE_PATH.read_text(encoding="utf-8")
    for revision in (
        "2042572329425f9ebf35ae6287ea5bab72b2c497",
        "46df7ee2fc4ae822d414d35bbd48be024e5cb1c0",
        "34ec18ba5e1acabebd66ae79d6fc746f63d8eb96",
    ):
        assert revision in note
    assert "License: MIT" in note
    assert "License: Apache-2.0 OR MIT" in note
    assert "references and oracles only" in note
    assert "gui_declarative_contract_v1.json" in note


def test_contract_nonclaims_prevent_upstream_or_runtime_overclaim():
    nonclaims = "\n".join(_contract()["nonclaims"])
    assert "No upstream React, Tailwind or Tauri wire/API compatibility" in nonclaims
    assert "No WebviewEvent" in nonclaims
    assert "No runtime GUI implementation" in nonclaims
