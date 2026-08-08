import pytest

from session_runtime import (
    Session,
    SessionEvent,
    SessionHeader,
    TodoItem,
    encode_todos,
)


def new_session(session_id="main"):
    return Session(SessionHeader(session_id, 100, "/workspace"))


def test_turn_events_are_contiguous_and_reconstruct_messages():
    session = new_session()
    turn = session.start_turn()
    session.append("user/message", "hello", turn=turn, source="human")
    step = session.start_step()
    session.append("assistant/chunk", "hi", turn=turn, step=step, metadata="text")
    session.append("assistant/message", "hi", turn=turn, step=step)
    session.end_step()
    session.end_turn("completed")

    assert [event.sequence for event in session.events] == list(range(1, 8))
    projection = session.projection()
    assert [(item.role, item.content) for item in projection.messages] == [
        ("user", "hello"),
        ("assistant", "hi"),
    ]
    assert session.derive_model_history() == [
        "user: hello",
        "assistant: hi",
    ]
    assert projection.last_turn_reason == "completed"


def test_unknown_events_must_be_marked_ignorable():
    with pytest.raises(ValueError, match="unknown required"):
        SessionEvent(1, "plugin/future")

    event = SessionEvent(1, "plugin/future", ignorable=True)
    session = new_session()
    session._restore_seed([event])
    assert session.projection().messages == []


def test_restore_rejects_non_contiguous_sequence():
    with pytest.raises(ValueError, match="not contiguous"):
        Session(SessionHeader("broken", 0), [SessionEvent(2, "turn/start")])


def test_fork_preserves_seed_lineage_and_can_continue():
    parent = new_session("parent")
    turn = parent.start_turn()
    parent.append("user/message", "seed", turn=turn)
    step = parent.start_step()
    parent.append("assistant/message", "answer", turn=turn, step=step)
    parent.end_step()
    parent.end_turn("completed")

    child = parent.fork("child", 200)
    next_turn = child.start_turn()

    assert child.header.parent_session == "parent"
    assert child.header.seed_length == parent.count()
    assert next_turn == 2
    assert child.events[-1].sequence == parent.count() + 1


def test_todo_plan_request_and_interaction_state_replay():
    session = new_session()
    todos = [TodoItem("port core", "completed"), TodoItem("port GUI", "in_progress")]
    session.append("todo/write", encode_todos(todos))
    session.append("plan/mode", "plan")
    session.append("request/header", "deepseek\tdeepseek-chat\tsystem")
    session.append("interaction/request", call_id="approval-1")
    session.append("interaction/request", call_id="question-1")
    session.append("interaction/resolve", call_id="approval-1")

    projection = session.projection()
    assert [(item.content, item.status) for item in projection.todos] == [
        ("port core", "completed"),
        ("port GUI", "in_progress"),
    ]
    assert projection.plan_mode == "plan"
    assert projection.request_provider == "deepseek"
    assert projection.request_model == "deepseek-chat"
    assert projection.open_interactions == ["question-1"]


def test_session_lifecycle_rejects_overlapping_turns_and_steps():
    session = new_session()
    session.start_turn()
    with pytest.raises(RuntimeError, match="already active"):
        session.start_turn()
    session.start_step()
    with pytest.raises(RuntimeError, match="already active"):
        session.start_step()
    with pytest.raises(RuntimeError, match="active step"):
        session.end_turn("completed")


def test_session_stats_replay_full_model_and_tool_timing():
    session = new_session()
    session.append("turn/start", turn=1, event_time=900)
    session.append("step/start", turn=1, step=1, event_time=1_000)
    session.append(
        "assistant/chunk", "", turn=1, step=1, metadata="text", event_time=1_100
    )
    session.append(
        "assistant/chunk", "a", turn=1, step=1, metadata="text", event_time=1_800
    )
    session.append(
        "tool/call", turn=1, step=1, call_id="call-a", event_time=2_000
    )
    session.append(
        "tool/call", turn=1, step=1, call_id="call-b", event_time=2_100
    )
    session.append(
        "tool/result", turn=1, step=1, call_id="call-b", event_time=3_100
    )
    session.append(
        "tool/result", turn=1, step=1, call_id="call-a", event_time=2_500
    )
    session.append(
        "assistant/message",
        "answer",
        turn=1,
        step=1,
        event_time=4_800,
        input_tokens=10,
        output_tokens=60,
    )
    session.append("step/end", turn=1, step=1, event_time=4_900)
    session.append("step/start", turn=1, step=2, event_time=5_000)
    session.append("step/end", turn=1, step=2, event_time=5_100)
    session.append("turn/end", turn=1, reason="completed", event_time=5_200)

    stats = session.projection().session_stats
    assert stats.turns == 1
    assert stats.steps == 2
    assert stats.llm_ms == 3_800
    assert stats.tool_ms == 1_500
    assert stats.ttft_ms == 800
    assert stats.ttft_steps == 1
    assert stats.decode_ms == 3_000
    assert stats.decode_tokens == 60


def test_session_stats_ignore_partial_steps_prune_calls_and_clamp_clock_skew():
    session = new_session()
    session.append("step/start", turn=1, step=1, event_time=2_000)
    session.append(
        "assistant/chunk", "partial", turn=1, step=1, event_time=2_100
    )
    session.append(
        "tool/call", turn=1, step=1, call_id="orphan", event_time=2_200
    )
    session.append("step/end", turn=1, step=1, event_time=2_300)
    session.append("turn/end", turn=1, reason="aborted", event_time=2_400)
    session.append(
        "tool/result", turn=1, step=1, call_id="orphan", event_time=9_000
    )
    session.append("step/start", turn=2, step=1, event_time=5_000)
    session.append(
        "assistant/message", "skew", turn=2, step=1, event_time=4_000
    )
    session.append("step/end", turn=2, step=1, event_time=5_100)

    stats = session.projection().session_stats
    assert stats.turns == 2
    assert stats.steps == 2
    assert stats.llm_ms == 0
    assert stats.tool_ms == 0
    assert stats.ttft_steps == 0
    assert stats.decode_tokens == 0
