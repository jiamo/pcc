"""Behavioral port tests for logged todo and plan state."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects" / "harness"
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent_runtime import AgentConfig, AgentLoop, PromptRuntime
from model_runtime import DeterministicModelProvider, ModelProviderRegistry
from session_runtime import Session, SessionHeader, fold_plan_mode
from todo_plan_runtime import PlanModeRuntime, TodoRuntime
from tool_runtime import ToolDefinition, ToolRuntime, ToolSchema


def new_session(name: str = "main") -> Session:
    return Session(SessionHeader(name, 1))


def test_todo_replaces_whole_list_replays_and_clears_on_next_turn() -> None:
    tools = ToolRuntime()
    TodoRuntime(tools, False)
    session = new_session()
    arguments = json.dumps(
        {
            "todos": [
                {"content": "Port core", "status": "completed"},
                {"content": "Port GUI", "status": "in_progress"},
            ]
        }
    )

    outcome = tools.execute("todo-1", "todo_write", arguments, None, session)

    assert not outcome.is_error
    assert outcome.content == "Updated todo list: 0 pending, 1 in progress, 1 completed."
    assert [(todo.content, todo.status) for todo in session.projection().todos] == [
        ("Port core", "completed"),
        ("Port GUI", "in_progress"),
    ]
    session.start_turn()
    assert session.projection().todos == []


@pytest.mark.parametrize(
    "todos,message",
    [
        ([{"content": " ", "status": "pending"}], "non-empty"),
        (
            [
                {"content": "same", "status": "pending"},
                {"content": "same", "status": "completed"},
            ],
            "duplicate",
        ),
        ([{"content": "x", "status": "unknown"}], "status"),
        ([{"content": "x", "status": "pending", "id": "x"}], "only"),
        (
            [
                {"content": "a", "status": "in_progress"},
                {"content": "b", "status": "in_progress"},
            ],
            "at most one",
        ),
    ],
)
def test_todo_validation_is_atomic(todos, message: str) -> None:
    tools = ToolRuntime()
    TodoRuntime(tools, False)
    session = new_session()

    outcome = tools.execute(
        "todo-bad", "todo_write", json.dumps({"todos": todos}), None, session
    )

    assert outcome.is_error
    assert message in outcome.content
    assert session.events == []


def test_plan_mode_is_logged_prompted_and_inherited_by_fork() -> None:
    tools = ToolRuntime()
    prompt = PromptRuntime()
    prompt.register("persona", 0, "Persona")
    plan = PlanModeRuntime(prompt, tools, "Plan carefully.", reviewer=lambda text, execution: "approve")
    session = new_session()

    assert plan.set(session, True) == "committed"
    assert fold_plan_mode(session.events) is True
    assert prompt.assemble(session) == "Persona\n\nPlan carefully."

    child = session.fork("child", 2)
    assert fold_plan_mode(child.events) is True
    assert plan.get(child) == {"active": True}


def test_mid_turn_plan_transition_commits_at_next_pre_step() -> None:
    tools = ToolRuntime()
    prompt = PromptRuntime()
    plan = PlanModeRuntime(prompt, tools, "Plan carefully.")
    session = new_session()
    session.start_turn()

    assert plan.set(session, True) == "queued"
    assert plan.get(session) == {"active": False, "pending": True}
    assert fold_plan_mode(session.events) is False

    plan.on_pre_step(session)
    assert fold_plan_mode(session.events) is True
    assert plan.get(session) == {"active": True}


def test_plan_mode_blocks_mutating_tools_and_reviewed_exit_is_logged() -> None:
    tools = ToolRuntime()
    prompt = PromptRuntime()
    plan = PlanModeRuntime(prompt, tools, "Plan carefully.", reviewer=lambda text, execution: "approve")
    tools.register(
        ToolDefinition(
            ToolSchema("write_file", "write"),
            lambda arguments, execution: "wrote",
            mutating=True,
        )
    )
    session = new_session()
    plan.set(session, True)

    denied = tools.execute("write-1", "write_file", "{}", None, session)
    assert denied.error_code == "PLAN_MODE_READ_ONLY"

    session.start_turn()
    approved = tools.execute(
        "plan-1",
        "exit_plan_mode",
        json.dumps({"plan": "# Port\n\nImplement it."}),
        None,
        session,
    )
    assert not approved.is_error
    assert plan.get(session) == {"active": True, "pending": False}
    plan.on_pre_step(session)
    assert fold_plan_mode(session.events) is False


def test_agent_pre_step_extension_applies_pending_plan_before_request() -> None:
    tools = ToolRuntime()
    TodoRuntime(tools, False)
    prompt = PromptRuntime()
    prompt.register("persona", 0, "Persona")
    plan = PlanModeRuntime(prompt, tools, "Plan carefully.")
    models = ModelProviderRegistry()
    models.register("deterministic", DeterministicModelProvider())
    session = new_session()
    agent = AgentLoop(
        session,
        prompt,
        tools,
        models,
        AgentConfig("deterministic", "pcc-keyless"),
    )
    plan.attach(agent)
    plan.set(session, True)

    agent.run_turn("hello")

    headers = [event for event in session.events if event.event_type == "request/header"]
    assert len(headers) == 1
    assert headers[0].text.endswith("Persona\n\nPlan carefully.")


@pytest.mark.integration
def test_current_pcc1_logged_plan() -> None:
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
