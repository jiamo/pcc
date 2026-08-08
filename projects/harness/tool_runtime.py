"""Tool definition/provider/consumer registry with execution policy hooks."""


class Cancellation:
    """Cooperative cancellation state shared across a tool execution."""

    def __init__(self) -> None:
        self.cancelled = False
        self.reason = ""

    def cancel(self, reason: str) -> None:
        self.cancelled = True
        self.reason = reason

    def require_active(self) -> None:
        if self.cancelled:
            raise RuntimeError("operation cancelled: " + self.reason)


class ToolSchema:
    """Model-facing tool declaration."""

    def __init__(self, name: str, description: str, parameters=None) -> None:
        if name == "":
            raise ValueError("tool name must not be empty")
        self.name = name
        self.description = description
        self.parameters = parameters or {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }


class ToolDefinition:
    """Complete tool provider including pure UI projection functions."""

    def __init__(
        self,
        schema: ToolSchema,
        execute,
        present_call=None,
        present_result=None,
        concurrency_safe=None,
        mutating: bool = False,
    ) -> None:
        self.schema = schema
        self.execute = execute
        self.present_call = present_call
        self.present_result = present_result
        self.concurrency_safe = concurrency_safe
        self.mutating = mutating


class ToolExecution:
    """Immutable call identity plus cooperative cancellation."""

    def __init__(
        self,
        call_id: str,
        name: str,
        arguments: str,
        cancellation: Cancellation,
        session=None,
        mutating: bool = False,
    ) -> None:
        self.call_id = call_id
        self.name = name
        self.arguments = arguments
        self.cancellation = cancellation
        self.session = session
        self.mutating = mutating


class ToolOutcome:
    """Normalized durable tool result."""

    def __init__(
        self,
        content: str,
        is_error: bool = False,
        error_name: str = "",
        error_code: str = "",
        metadata: str = "",
    ) -> None:
        self.content = content
        self.is_error = is_error
        self.error_name = error_name
        self.error_code = error_code
        self.metadata = metadata


class ToolRegistration:
    """Idempotent disposer returned by tool registration."""

    def __init__(self, registry, name: str) -> None:
        self.registry = registry
        self.name = name
        self.active = True

    def dispose(self) -> None:
        if not self.active:
            return
        self.active = False
        self.registry.unregister(self.name)


class ToolRuntime:
    """Tool registry and pre/around/post execution pipeline."""

    def __init__(self) -> None:
        self.names = []
        self.definitions = []
        self.pre_hooks = []
        self.around_hooks = []
        self.post_hooks = []
        self.result_listeners = []

    def register(self, definition: ToolDefinition) -> ToolRegistration:
        name = definition.schema.name
        if self.index_of(name) >= 0:
            raise ValueError("tool already registered: " + name)
        self.names.append(name)
        self.definitions.append(definition)
        return ToolRegistration(self, name)

    def unregister(self, name: str) -> None:
        index = self.index_of(name)
        if index < 0:
            return
        self.names.pop(index)
        self.definitions.pop(index)

    def add_pre_hook(self, callback) -> None:
        self.pre_hooks.append(callback)

    def add_around_hook(self, callback) -> None:
        self.around_hooks.append(callback)

    def add_post_hook(self, callback) -> None:
        self.post_hooks.append(callback)

    def on_result(self, callback) -> None:
        self.result_listeners.append(callback)

    def schemas(self):
        schemas = []
        i = 0
        while i < len(self.definitions):
            schemas.append(self.definitions[i].schema)
            i += 1
        return schemas

    def execute(
        self,
        call_id: str,
        name: str,
        arguments: str,
        cancellation=None,
        session=None,
    ) -> ToolOutcome:
        if cancellation is None:
            cancellation = Cancellation()
        index = self.index_of(name)
        if index < 0:
            return ToolOutcome(
                "unknown tool: " + name, True, "UnknownTool", "UNKNOWN_TOOL"
            )
        definition = self.definitions[index]
        execution = ToolExecution(
            call_id,
            name,
            arguments,
            cancellation,
            session,
            definition.mutating,
        )
        outcome = self._run_pre(0, definition, execution)
        listeners = self.result_listeners.copy()
        i = 0
        while i < len(listeners):
            listeners[i](execution, outcome)
            i += 1
        return outcome

    def _run_pre(self, index: int, definition, execution) -> ToolOutcome:
        if index >= len(self.pre_hooks):
            return self._run_around(0, definition, execution)

        def next_hook():
            return self._run_pre(index + 1, definition, execution)

        result = self.pre_hooks[index](execution, next_hook)
        if result is None:
            return ToolOutcome(
                "tool execution denied", True, "PermissionDenied", "DENIED"
            )
        return result

    def _run_around(self, index: int, definition, execution) -> ToolOutcome:
        if index >= len(self.around_hooks):
            return self._run_body(definition, execution)

        def next_hook():
            return self._run_around(index + 1, definition, execution)

        return self.around_hooks[index](execution, next_hook)

    def _run_body(self, definition, execution) -> ToolOutcome:
        try:
            execution.cancellation.require_active()
            value = definition.execute(execution.arguments, execution)
            execution.cancellation.require_active()
            if isinstance(value, ToolOutcome):
                outcome = value
            else:
                outcome = ToolOutcome(str(value))
        except Exception as error:
            outcome = ToolOutcome(
                str(error), True, error.__class__.__name__, "TOOL_ERROR"
            )
        return self._run_post(0, execution, outcome)

    def _run_post(self, index: int, execution, outcome) -> ToolOutcome:
        if index >= len(self.post_hooks):
            return outcome

        def next_hook(next_outcome=outcome):
            return self._run_post(index + 1, execution, next_outcome)

        result = self.post_hooks[index](execution, outcome, next_hook)
        if result is None:
            return ToolOutcome(
                "tool result blocked", True, "ResultBlocked", "BLOCKED"
            )
        return result

    def index_of(self, name: str) -> int:
        i = 0
        while i < len(self.names):
            if self.names[i] == name:
                return i
            i += 1
        return -1


def create_default_tools() -> ToolRuntime:
    """Create the keyless built-in tool composition."""
    runtime = ToolRuntime()

    def echo(arguments: str, execution) -> str:
        return arguments

    runtime.register(
        ToolDefinition(
            ToolSchema(
                "echo",
                "Return the supplied text",
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            ),
            echo,
        )
    )
    return runtime
