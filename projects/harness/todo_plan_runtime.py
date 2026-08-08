"""Logged todo replacement and plan collaboration state."""

import json

from session_runtime import TodoItem, encode_plan_mode, encode_todos, fold_plan_mode
from tool_runtime import ToolDefinition, ToolOutcome, ToolSchema


TODO_STATUSES = ["pending", "in_progress", "completed"]


class TodoRuntime:
    """Model-facing whole-list todo replacement over session events."""

    def __init__(self, tools, allow_parallel_in_progress: bool) -> None:
        self.tools = tools
        self.allow_parallel_in_progress = allow_parallel_in_progress
        tools.register(
            ToolDefinition(
                ToolSchema(
                    "todo_write",
                    self._description(),
                    {
                        "type": "object",
                        "properties": {
                            "todos": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "content": {"type": "string"},
                                        "status": {
                                            "type": "string",
                                            "enum": TODO_STATUSES,
                                        },
                                    },
                                    "required": ["content", "status"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["todos"],
                        "additionalProperties": False,
                    },
                ),
                self.execute,
                mutating=False,
            )
        )

    def execute(self, arguments: str, execution) -> ToolOutcome:
        if execution.session is None:
            raise RuntimeError("todo_write requires an owning agent session")
        try:
            payload = json.loads(arguments)
        except Exception as error:
            raise ValueError("invalid todo_write JSON") from error
        if not isinstance(payload, dict) or set(payload.keys()) != {"todos"}:
            raise ValueError("invalid todo_write input: expected only todos")
        todos = validate_todos(payload["todos"], self.allow_parallel_in_progress)
        execution.session.append("todo/write", encode_todos(todos))
        pending = count_status(todos, "pending")
        active = count_status(todos, "in_progress")
        completed = count_status(todos, "completed")
        result = {
            "todos": todo_records(todos),
            "counts": {
                "pending": pending,
                "inProgress": active,
                "completed": completed,
            },
        }
        return ToolOutcome(
            "Updated todo list: "
            + str(pending)
            + " pending, "
            + str(active)
            + " in progress, "
            + str(completed)
            + " completed.",
            metadata=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        )

    def _description(self) -> str:
        policy = (
            "Mark every actively worked task in_progress; several may be active."
            if self.allow_parallel_in_progress
            else "Keep at most one task in_progress."
        )
        return (
            "Replace the current agent session's ENTIRE todo list. "
            + policy
            + " Statuses are pending, in_progress, and completed."
        )


class PlanPolicyRenderer:
    """Session-aware prompt section owned by plan mode."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def render(self, session) -> str:
        if session is None:
            return ""
        return self.runtime.section if self.runtime.effective(session) else ""


class PlanModeRuntime:
    """Logged plan state, pending in-turn selections and exit review tool."""

    def __init__(
        self,
        prompt,
        tools,
        section: str,
        reviewer=None,
        enforce_read_only: bool = True,
    ) -> None:
        if not isinstance(section, str) or section.strip() == "":
            raise ValueError("plan mode section must be a non-empty string")
        self.section = section
        self.reviewer = reviewer
        self.enforce_read_only = enforce_read_only
        self.pending_sessions = []
        self.pending_values = []
        prompt.register_dynamic("plan:policy", 50, PlanPolicyRenderer(self))
        tools.add_pre_hook(self.guard_tool)
        tools.register(
            ToolDefinition(
                ToolSchema(
                    "exit_plan_mode",
                    "Present a complete markdown plan for user review and exit on approval.",
                    {
                        "type": "object",
                        "properties": {"plan": {"type": "string"}},
                        "required": ["plan"],
                        "additionalProperties": False,
                    },
                ),
                self.exit_plan_mode,
                mutating=False,
            )
        )

    def attach(self, agent) -> None:
        agent.add_pre_step_hook(self.on_pre_step)

    def get(self, session):
        active = fold_plan_mode(session.events)
        index = self._pending_index(session)
        if index < 0:
            return {"active": active}
        return {"active": active, "pending": self.pending_values[index]}

    def effective(self, session) -> bool:
        index = self._pending_index(session)
        if index >= 0:
            return self.pending_values[index]
        return fold_plan_mode(session.events)

    def set(self, session, active: bool) -> str:
        logged = fold_plan_mode(session.events)
        index = self._pending_index(session)
        current = self.pending_values[index] if index >= 0 else logged
        if active == current:
            return "noop"
        if session.active_turn != 0:
            if index < 0:
                self.pending_sessions.append(session)
                self.pending_values.append(active)
            else:
                self.pending_values[index] = active
            return "cancelled" if active == logged else "queued"
        if active == logged:
            self._remove_pending(session)
            return "cancelled"
        session.append("plan/mode", encode_plan_mode(active))
        self._remove_pending(session)
        return "committed"

    def on_pre_step(self, session) -> None:
        index = self._pending_index(session)
        if index < 0:
            return
        active = self.pending_values[index]
        if active != fold_plan_mode(session.events):
            session.append("plan/mode", encode_plan_mode(active))
        self.pending_sessions.pop(index)
        self.pending_values.pop(index)

    def guard_tool(self, execution, next_):
        if (
            self.enforce_read_only
            and execution.session is not None
            and self.effective(execution.session)
            and execution.mutating
        ):
            return ToolOutcome(
                "mutating tool is unavailable while plan mode is active",
                True,
                "PlanModeReadOnly",
                "PLAN_MODE_READ_ONLY",
            )
        return next_()

    def exit_plan_mode(self, arguments: str, execution) -> ToolOutcome:
        session = execution.session
        if session is None:
            raise RuntimeError("exit_plan_mode requires a calling agent session")
        if not fold_plan_mode(session.events):
            raise RuntimeError("exit_plan_mode is only available in plan mode")
        try:
            payload = json.loads(arguments)
        except Exception as error:
            raise ValueError("exit_plan_mode requires valid JSON") from error
        if not isinstance(payload, dict) or set(payload.keys()) != {"plan"}:
            raise ValueError("exit_plan_mode input must contain only plan")
        plan = payload["plan"]
        if not isinstance(plan, str) or not starts_with_heading(plan.strip()):
            raise ValueError(
                "exit_plan_mode requires a non-empty markdown plan starting with a # heading"
            )
        if self.reviewer is None:
            raise RuntimeError("no user-questions channel is available to review the plan")
        decision = self.reviewer(plan, execution)
        if decision != "approve":
            feedback = "" if decision is None else str(decision)
            if feedback == "":
                raise RuntimeError("the user chose to keep planning")
            raise RuntimeError("the user chose to keep planning: " + feedback)
        self.set(session, False)
        return ToolOutcome(
            "Plan approved — plan mode exited; carry out the plan starting with your next step.",
            metadata='{"approved":true}',
        )

    def _pending_index(self, session) -> int:
        i = 0
        while i < len(self.pending_sessions):
            if self.pending_sessions[i] is session:
                return i
            i += 1
        return -1

    def _remove_pending(self, session) -> None:
        index = self._pending_index(session)
        if index >= 0:
            self.pending_sessions.pop(index)
            self.pending_values.pop(index)


def validate_todos(raw, allow_parallel: bool):
    if not isinstance(raw, list):
        raise ValueError("invalid todos: todos must be a list")
    todos = []
    seen = []
    active = 0
    i = 0
    while i < len(raw):
        item = raw[i]
        if not isinstance(item, dict) or set(item.keys()) != {"content", "status"}:
            raise ValueError("invalid todo: expected only content and status")
        content = item["content"]
        status = item["status"]
        if not isinstance(content, str) or content.strip() == "":
            raise ValueError("invalid todo: content must be a non-empty string")
        content = content.strip()
        if content in seen:
            raise ValueError("invalid todos: duplicate content " + json.dumps(content))
        if status not in TODO_STATUSES:
            raise ValueError("invalid todo status: " + str(status))
        seen.append(content)
        if status == "in_progress":
            active += 1
        todos.append(TodoItem(content, status))
        i += 1
    if not allow_parallel and active > 1:
        raise ValueError(
            "invalid todos: at most one task may be in_progress (got " + str(active) + ")"
        )
    return todos


def count_status(todos, status: str) -> int:
    count = 0
    i = 0
    while i < len(todos):
        if todos[i].status == status:
            count += 1
        i += 1
    return count


def todo_records(todos):
    records = []
    i = 0
    while i < len(todos):
        records.append({"content": todos[i].content, "status": todos[i].status})
        i += 1
    return records


def starts_with_heading(plan: str) -> bool:
    if not plan.startswith("#"):
        return False
    index = 0
    while index < len(plan) and plan[index] == "#":
        index += 1
    return index <= 6 and index < len(plan) and plan[index] == " " and plan[index + 1 :].strip() != ""
