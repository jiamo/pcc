import pytest

from agent_runtime import AgentConfig, AgentLoop, PromptRuntime
from model_runtime import (
    DeterministicModelProvider,
    ModelChunk,
    ModelProviderRegistry,
    ModelResult,
)
from session_runtime import Session, SessionHeader
from tool_runtime import ToolRuntime, create_default_tools


def create_agent(provider=None):
    session = Session(SessionHeader("main", 0))
    prompt = PromptRuntime()
    prompt.register("tools", 200, "Use tools when needed.")
    prompt.register("persona", 0, "You are PCC Harness.")
    tools = create_default_tools()
    models = ModelProviderRegistry()
    models.register("local", provider or DeterministicModelProvider())
    agent = AgentLoop(session, prompt, tools, models, AgentConfig("local", "test"))
    return agent


def test_assistant_turn_logs_request_chunks_message_and_lifecycle():
    agent = create_agent()

    response = agent.run_turn("hello")

    assert response == "PCC harness is running. You said: hello"
    types = [event.event_type for event in agent.session.events]
    assert types == [
        "turn/start",
        "user/message",
        "step/start",
        "request/header",
        "assistant/chunk",
        "assistant/message",
        "step/end",
        "turn/end",
    ]
    projection = agent.session.projection()
    assert projection.request_system == "You are PCC Harness.\n\nUse tools when needed."


def test_agent_constructor_declares_concrete_capability_dependencies():
    annotations = AgentLoop.__init__.__annotations__

    assert annotations["session"] is Session
    assert annotations["prompt"] is PromptRuntime
    assert annotations["tools"] is ToolRuntime
    assert annotations["models"] is ModelProviderRegistry
    assert annotations["config"] is AgentConfig


def test_prompt_order_is_stable_without_dynamic_sort_callbacks():
    prompt = PromptRuntime()
    prompt.register("late-a", 10, "A")
    prompt.register("first", 0, "First")
    prompt.register("late-b", 10, "B")

    assert prompt.assemble() == "First\n\nA\n\nB"


def test_tool_turn_runs_second_model_step_and_replays_call_pair():
    agent = create_agent()

    response = agent.run_turn("/tool echo native pcc")

    assert response == "Tool returned: native pcc"
    projection = agent.session.projection()
    assert [(message.role, message.content) for message in projection.messages] == [
        ("user", "/tool echo native pcc"),
        ("assistant-tool-call", ""),
        ("tool", "native pcc"),
        ("assistant", "Tool returned: native pcc"),
    ]
    assert [event.step for event in agent.session.events if event.event_type == "step/start"] == [1, 2]


def test_cancel_before_model_call_closes_turn_as_aborted():
    agent = create_agent()
    agent.cancel()

    assert agent.run_turn("hello") == ""
    assert agent.session.events[-1].reason == "aborted"


class BrokenProvider:
    def complete(self, request):
        raise RuntimeError("provider failed")


def test_provider_failure_closes_step_and_turn_before_propagating():
    agent = create_agent(BrokenProvider())

    with pytest.raises(RuntimeError, match="provider failed"):
        agent.run_turn("hello")

    assert [event.event_type for event in agent.session.events[-2:]] == [
        "step/end",
        "turn/end",
    ]
    assert agent.session.events[-1].reason == "error"


class UsageProvider:
    def complete(self, request):
        return ModelResult(
            "measured",
            [],
            [ModelChunk("text", "measured")],
            input_tokens=7,
            output_tokens=11,
        )


def test_agent_logs_provider_usage_for_replayable_session_stats():
    agent = create_agent(UsageProvider())

    assert agent.run_turn("measure") == "measured"

    messages = [
        event
        for event in agent.session.events
        if event.event_type == "assistant/message"
    ]
    assert len(messages) == 1
    assert messages[0].input_tokens == 7
    assert messages[0].output_tokens == 11
    assert agent.session.projection().session_stats.decode_tokens == 11
