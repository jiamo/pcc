"""Event-sourced Harness agent loop over model and tool capability seams."""

from model_runtime import (
    ModelMessage,
    ModelProviderRegistry,
    ModelRequest,
)
from session_runtime import Session
from tool_runtime import ToolRuntime


class PromptSection:
    """One named ordered system-prompt section."""

    def __init__(self, name: str, order: int, text: str, renderer=None) -> None:
        self.name = name
        self.order = order
        self.text = text
        self.renderer = renderer

    def render(self, session=None) -> str:
        if self.renderer is None:
            return self.text
        return self.renderer.render(session)


class PromptRuntime:
    """Ordered, duplicate-safe prompt section registry."""

    def __init__(self) -> None:
        self.sections = []

    def register(self, name: str, order: int, text: str) -> None:
        self._require_new(name)
        self.sections.append(PromptSection(name, order, text))

    def register_dynamic(self, name: str, order: int, renderer) -> None:
        """Register a session-aware prompt renderer through a stable object."""
        self._require_new(name)
        self.sections.append(PromptSection(name, order, "", renderer))

    def _require_new(self, name: str) -> None:
        i = 0
        while i < len(self.sections):
            if self.sections[i].name == name:
                raise ValueError("prompt section already registered: " + name)
            i += 1

    def assemble(self, session=None) -> str:
        ordered = []
        i = 0
        while i < len(self.sections):
            section = self.sections[i]
            insert_at = len(ordered)
            j = 0
            while j < len(ordered):
                if section.order < ordered[j].order:
                    insert_at = j
                    break
                j += 1
            ordered.insert(insert_at, section)
            i += 1
        out = ""
        i = 0
        while i < len(ordered):
            rendered = ordered[i].render(session)
            if rendered == "":
                i += 1
                continue
            if out != "":
                out += "\n\n"
            out += rendered
            i += 1
        return out


class AgentConfig:
    """Resolved deployment settings for one agent composition."""

    def __init__(
        self,
        provider: str,
        model: str,
        max_steps: int = 16,
        max_tokens: int = 4096,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        self.provider = provider
        self.model = model
        self.max_steps = max_steps
        self.max_tokens = max_tokens


class AgentLoop:
    """Default multi-step model/tool driver with fully logged inputs."""

    def __init__(
        self,
        session: Session,
        prompt: PromptRuntime,
        tools: ToolRuntime,
        models: ModelProviderRegistry,
        config: AgentConfig,
    ) -> None:
        self.session = session
        self.prompt = prompt
        self.tools = tools
        self.models = models
        self.config = config
        self.cancelled = False
        self.cancel_reason = ""
        self.pre_step_hooks = []

    def add_pre_step_hook(self, callback) -> None:
        """Register a lifecycle extension run before each accepted step."""
        self.pre_step_hooks.append(callback)

    def cancel(self, reason: str = "user") -> None:
        self.cancelled = True
        self.cancel_reason = reason

    def run_turn(self, user_text: str) -> str:
        if user_text == "":
            raise ValueError("user message must not be empty")
        turn = self.session.start_turn()
        self.session.append(
            "user/message", user_text, turn=turn, source="human"
        )
        response = ""
        try:
            step_count = 0
            needs_model = True
            while needs_model:
                if self.cancelled:
                    self.session.end_turn("aborted")
                    return response
                if step_count >= self.config.max_steps:
                    self.session.end_turn("blocked")
                    return response
                self._run_pre_step_hooks()
                step = self.session.start_step()
                system = self.prompt.assemble(self.session)
                self.session.append(
                    "request/header",
                    self.config.provider
                    + "\t"
                    + self.config.model
                    + "\t"
                    + system,
                    turn=turn,
                    step=step,
                )
                request = ModelRequest(
                    self.config.provider,
                    self.config.model,
                    system,
                    self._model_messages(),
                    self.tools.schemas(),
                    self.config.max_tokens,
                )
                result = self.models.complete(request)
                self._append_chunks(turn, step, result.chunks)
                if len(result.tool_calls) == 0:
                    response = result.text
                    self.session.append(
                        "assistant/message",
                        response,
                        turn=turn,
                        step=step,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                    )
                    self.session.end_step()
                    needs_model = False
                else:
                    i = 0
                    while i < len(result.tool_calls):
                        call = result.tool_calls[i]
                        self.session.append(
                            "tool/call",
                            turn=turn,
                            step=step,
                            call_id=call.call_id,
                            name=call.name,
                            arguments=call.arguments,
                        )
                        outcome = self.tools.execute(
                            call.call_id, call.name, call.arguments, None, self.session
                        )
                        self.session.append(
                            "tool/result",
                            outcome.content,
                            turn=turn,
                            step=step,
                            call_id=call.call_id,
                            name=call.name,
                            reason="error" if outcome.is_error else "completed",
                            metadata=outcome.metadata,
                        )
                        i += 1
                    self.session.end_step()
                step_count += 1
            self.session.end_turn("completed")
            return response
        except Exception:
            if self.session.active_step != 0:
                self.session.end_step()
            if self.session.active_turn != 0:
                self.session.end_turn("error")
            raise

    def _run_pre_step_hooks(self) -> None:
        hooks = self.pre_step_hooks.copy()
        i = 0
        while i < len(hooks):
            hooks[i](self.session)
            i += 1

    def _append_chunks(self, turn: int, step: int, chunks) -> None:
        i = 0
        while i < len(chunks):
            chunk = chunks[i]
            self.session.append(
                "assistant/chunk",
                chunk.text,
                turn=turn,
                step=step,
                call_id=chunk.call_id,
                name=chunk.name,
                arguments=chunk.arguments,
                metadata=chunk.kind,
            )
            i += 1

    def _model_messages(self):
        projection = self.session.projection()
        messages = []
        i = 0
        while i < len(projection.messages):
            message = projection.messages[i]
            if message.role == "assistant-tool-call":
                messages.append(
                    ModelMessage(
                        "assistant",
                        "",
                        message.call_id,
                        message.name,
                        message.arguments,
                    )
                )
            else:
                messages.append(
                    ModelMessage(
                        message.role,
                        message.content,
                        message.call_id,
                        message.name,
                        message.arguments,
                    )
                )
            i += 1
        return messages
