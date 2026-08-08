import json

import pytest

from session_persistence import JsonlSessionStore
from session_runtime import Session, SessionHeader


def populated_session():
    session = Session(SessionHeader("main", 123, "/workspace", agent_preset="headless"))
    turn = session.start_turn()
    session.append("user/message", "hello 世界", turn=turn)
    step = session.start_step()
    session.append("assistant/message", "hi", turn=turn, step=step)
    session.end_step()
    session.end_turn("completed")
    return session


def test_jsonl_round_trip_preserves_header_events_and_continuation(tmp_path):
    store = JsonlSessionStore(str(tmp_path))
    source = populated_session()

    store.save(source)
    restored = store.load("main")
    next_turn = restored.start_turn()

    assert restored.header.cwd == "/workspace"
    assert restored.header.agent_preset == "headless"
    assert restored.events[1].text == "hello 世界"
    assert restored.events[1].event_time == source.events[1].event_time
    assert next_turn == 2
    assert restored.events[-1].sequence == source.count() + 1
    assert store.list_ids() == ["main"]


def test_store_rejects_path_traversal_ids(tmp_path):
    store = JsonlSessionStore(str(tmp_path))
    for session_id in ("../escape", "nested/id", "nested\\id", ""):
        with pytest.raises(ValueError, match="session id"):
            store.path_for(session_id)


def test_load_rejects_unknown_required_event(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(
        json.dumps({"kind": "header", "version": 0, "id": "bad", "createdAt": 0})
        + "\n"
        + json.dumps(
            {
                "kind": "event",
                "sequence": 1,
                "type": "future/required",
                "time": 0,
                "inputTokens": -1,
                "outputTokens": -1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown required"):
        JsonlSessionStore(str(tmp_path)).load("bad")


def test_delete_is_idempotent(tmp_path):
    store = JsonlSessionStore(str(tmp_path))
    store.save(populated_session())

    assert store.delete("main") is True
    assert store.delete("main") is False


def test_jsonl_round_trip_preserves_model_usage(tmp_path):
    session = Session(SessionHeader("usage", 1))
    session.append(
        "assistant/message",
        "answer",
        turn=1,
        step=1,
        event_time=1234,
        input_tokens=7,
        output_tokens=11,
    )
    store = JsonlSessionStore(str(tmp_path))

    store.save(session)
    event = store.load("usage").events[0]

    assert event.event_time == 1234
    assert event.input_tokens == 7
    assert event.output_tokens == 11


def test_load_rejects_pre_stats_event_records_without_time(tmp_path):
    path = tmp_path / "old.jsonl"
    path.write_text(
        json.dumps({"kind": "header", "version": 0, "id": "old", "createdAt": 0})
        + "\n"
        + json.dumps({"kind": "event", "sequence": 1, "type": "turn/start"})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="integer: time"):
        JsonlSessionStore(str(tmp_path)).load("old")
