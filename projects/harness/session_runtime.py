"""Versioned event-sourced sessions and replay projections."""

import time


SESSION_FORMAT_VERSION = 0

KNOWN_EVENT_TYPES = [
    "turn/start",
    "turn/end",
    "step/start",
    "step/end",
    "request/header",
    "user/message",
    "assistant/chunk",
    "assistant/message",
    "tool/call",
    "tool/result",
    "todo/write",
    "plan/mode",
    "interaction/request",
    "interaction/resolve",
    "compaction/start",
    "compaction/end",
]


class SessionHeader:
    """Validated immutable-intent metadata stored before a session log."""

    def __init__(
        self,
        session_id: str,
        created_at: int,
        cwd: str = "",
        parent_session: str = "",
        seed_length: int = 0,
        origin: str = "",
        delegation_depth: int = 0,
        agent_preset: str = "",
        version: int = SESSION_FORMAT_VERSION,
    ) -> None:
        if session_id == "":
            raise ValueError("session id must not be empty")
        if created_at < 0:
            raise ValueError("session created_at must be non-negative")
        if version != SESSION_FORMAT_VERSION:
            raise ValueError("unsupported session format version: " + str(version))
        if seed_length < 0:
            raise ValueError("session seed_length must be non-negative")
        if delegation_depth < 0:
            raise ValueError("delegation_depth must be non-negative")
        if origin != "" and origin != "subagent":
            raise ValueError("session origin must be empty or subagent")
        self.version = version
        self.session_id = session_id
        self.created_at = created_at
        self.cwd = cwd
        self.parent_session = parent_session
        self.seed_length = seed_length
        self.origin = origin
        self.delegation_depth = delegation_depth
        self.agent_preset = agent_preset


class SessionEvent:
    """One lossless ordered session fact.

    PCC's native storage codec uses explicit scalar fields instead of retaining
    arbitrary host-language objects across the durable boundary.
    """

    def __init__(
        self,
        sequence: int,
        event_type: str,
        text: str = "",
        turn: int = 0,
        step: int = 0,
        call_id: str = "",
        name: str = "",
        arguments: str = "",
        reason: str = "",
        source: str = "",
        metadata: str = "",
        ignorable: bool = False,
        event_time: int = 0,
        input_tokens: int = -1,
        output_tokens: int = -1,
    ) -> None:
        if sequence <= 0:
            raise ValueError("event sequence must be positive")
        if event_type == "":
            raise ValueError("event type must not be empty")
        if not is_known_event_type(event_type) and not ignorable:
            raise ValueError("unknown required session event: " + event_type)
        if event_time < 0:
            raise ValueError("event time must be non-negative")
        if input_tokens < -1 or output_tokens < -1:
            raise ValueError("token usage must be non-negative or absent")
        self.sequence = sequence
        self.event_type = event_type
        self.text = text
        self.turn = turn
        self.step = step
        self.call_id = call_id
        self.name = name
        self.arguments = arguments
        self.reason = reason
        self.source = source
        self.metadata = metadata
        self.ignorable = ignorable
        self.event_time = event_time
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def copy(self):
        return SessionEvent(
            self.sequence,
            self.event_type,
            self.text,
            self.turn,
            self.step,
            self.call_id,
            self.name,
            self.arguments,
            self.reason,
            self.source,
            self.metadata,
            self.ignorable,
            self.event_time,
            self.input_tokens,
            self.output_tokens,
        )


class MessageProjection:
    """One reconstructed model-visible message."""

    def __init__(
        self,
        role: str,
        content: str,
        call_id: str = "",
        name: str = "",
        arguments: str = "",
    ) -> None:
        self.role = role
        self.content = content
        self.call_id = call_id
        self.name = name
        self.arguments = arguments


class TodoItem:
    """One replayable todo item."""

    def __init__(self, content: str, status: str) -> None:
        if status != "pending" and status != "in_progress" and status != "completed":
            raise ValueError("unsupported todo status: " + status)
        self.content = content
        self.status = status


class SessionStatsProjection:
    """Whole-log lifecycle, model and tool timing totals."""

    def __init__(self) -> None:
        self.turns = 0
        self.steps = 0
        self.llm_ms = 0
        self.tool_ms = 0
        self.ttft_ms = 0
        self.ttft_steps = 0
        self.decode_ms = 0
        self.decode_tokens = 0


class SessionProjection:
    """Derived current state reconstructed entirely from the event log."""

    def __init__(self) -> None:
        self.messages = []
        self.todos = []
        self.title = "New Session"
        self.plan_mode = "default"
        self.plan_mode_active = False
        self.request_provider = ""
        self.request_model = ""
        self.request_system = ""
        self.open_interactions = []
        self.event_count = 0
        self.completed_turn_count = 0
        self.error_turn_count = 0
        self.step_count = 0
        self.assistant_chunk_count = 0
        self.tool_call_count = 0
        self.last_turn = 0
        self.last_step = 0
        self.last_turn_reason = ""
        self.session_stats = SessionStatsProjection()
        self.stats_last_turn = 0
        self.stats_open_turn = 0
        self.stats_open_step = 0
        self.stats_step_started_at = 0
        self.stats_first_token_at = -1
        self.stats_pending_call_ids = []
        self.stats_pending_call_times = []


class Session:
    """Append-only session with fork, restore, observer, and replay support."""

    def __init__(self, header: SessionHeader, seed=None, clock=None) -> None:
        self.header = header
        self.events = []
        self.listeners = []
        self.next_sequence = 1
        self.next_turn = 1
        self.active_turn = 0
        self.active_step = 0
        self.clock = clock
        if seed is not None:
            self._restore_seed(seed)

    def _restore_seed(self, seed) -> None:
        expected = 1
        i = 0
        while i < len(seed):
            event = seed[i]
            if event.sequence != expected:
                raise ValueError("session event sequence is not contiguous")
            copied = event.copy()
            self.events.append(copied)
            if copied.turn >= self.next_turn:
                self.next_turn = copied.turn + 1
            expected += 1
            i += 1
        self.next_sequence = expected

    def on_event(self, callback) -> None:
        self.listeners.append(callback)

    def append(
        self,
        event_type: str,
        text: str = "",
        turn: int = 0,
        step: int = 0,
        call_id: str = "",
        name: str = "",
        arguments: str = "",
        reason: str = "",
        source: str = "",
        metadata: str = "",
        ignorable: bool = False,
        event_time: int = -1,
        input_tokens: int = -1,
        output_tokens: int = -1,
    ) -> SessionEvent:
        if event_time < 0:
            event_time = self._now_ms()
        event = SessionEvent(
            self.next_sequence,
            event_type,
            text,
            turn,
            step,
            call_id,
            name,
            arguments,
            reason,
            source,
            metadata,
            ignorable,
            event_time,
            input_tokens,
            output_tokens,
        )
        self.events.append(event)
        self.next_sequence += 1
        listeners = self.listeners.copy()
        i = 0
        while i < len(listeners):
            listeners[i](self, event)
            i += 1
        return event

    def _now_ms(self) -> int:
        if self.clock is not None:
            return self.clock.now_ms()
        return int(time.time() * 1000.0)

    def start_turn(self) -> int:
        if self.active_turn != 0:
            raise RuntimeError("a session turn is already active")
        turn = self.next_turn
        self.next_turn += 1
        self.active_turn = turn
        self.active_step = 0
        self.append("turn/start", turn=turn)
        return turn

    def start_step(self) -> int:
        if self.active_turn == 0:
            raise RuntimeError("cannot start a step without an active turn")
        if self.active_step != 0:
            raise RuntimeError("a session step is already active")
        step = 1
        i = len(self.events) - 1
        while i >= 0:
            event = self.events[i]
            if event.turn != self.active_turn:
                i -= 1
                continue
            if event.step >= step:
                step = event.step + 1
            i -= 1
        self.active_step = step
        self.append("step/start", turn=self.active_turn, step=step)
        return step

    def end_step(self) -> None:
        if self.active_step == 0:
            raise RuntimeError("no session step is active")
        self.append(
            "step/end", turn=self.active_turn, step=self.active_step
        )
        self.active_step = 0

    def end_turn(self, reason: str) -> None:
        if self.active_turn == 0:
            raise RuntimeError("no session turn is active")
        if self.active_step != 0:
            raise RuntimeError("cannot end a turn with an active step")
        if not is_turn_end_reason(reason):
            raise ValueError("unsupported turn end reason: " + reason)
        turn = self.active_turn
        self.append("turn/end", turn=turn, reason=reason)
        self.active_turn = 0

    def projection(self) -> SessionProjection:
        projection = SessionProjection()
        i = 0
        while i < len(self.events):
            fold_event(projection, self.events[i])
            i += 1
        return projection

    def derive_model_history(self):
        """Render the compact role-prefixed transcript used by CLI and GUI."""
        projection = self.projection()
        history = []
        i = 0
        while i < len(projection.messages):
            message = projection.messages[i]
            if message.role == "user":
                history.append("user: " + message.content)
            elif message.role == "assistant":
                history.append("assistant: " + message.content)
            elif message.role == "tool":
                history.append("tool: " + message.content)
            i += 1
        return history

    def fork(self, session_id: str, created_at: int, event_count: int = -1):
        if event_count < 0:
            event_count = len(self.events)
        if event_count > len(self.events):
            raise ValueError("fork event_count exceeds source log")
        seed = []
        i = 0
        while i < event_count:
            seed.append(self.events[i].copy())
            i += 1
        header = SessionHeader(
            session_id,
            created_at,
            self.header.cwd,
            self.header.session_id,
            event_count,
            "",
            self.header.delegation_depth,
            self.header.agent_preset,
        )
        return Session(header, seed, self.clock)

    def count(self) -> int:
        return len(self.events)


def is_known_event_type(event_type: str) -> bool:
    i = 0
    while i < len(KNOWN_EVENT_TYPES):
        if KNOWN_EVENT_TYPES[i] == event_type:
            return True
        i += 1
    return False


def is_turn_end_reason(reason: str) -> bool:
    return (
        reason == "completed"
        or reason == "aborted"
        or reason == "blocked"
        or reason == "error"
        or reason == "max-tokens"
        or reason == "interrupted"
    )


def fold_event(projection: SessionProjection, event: SessionEvent) -> None:
    """Apply one known event to a replay projection."""
    projection.event_count += 1
    if event.event_type == "user/message":
        projection.messages.append(MessageProjection("user", event.text))
        if projection.title == "New Session":
            projection.title = derive_session_title(event.text)
    elif event.event_type == "assistant/message":
        projection.messages.append(MessageProjection("assistant", event.text))
        fold_assistant_stats(projection, event)
    elif event.event_type == "assistant/chunk":
        projection.assistant_chunk_count += 1
        if (
            projection.stats_open_turn == event.turn
            and projection.stats_open_step == event.step
            and projection.stats_first_token_at < 0
            and event.text != ""
        ):
            projection.stats_first_token_at = event.event_time
    elif event.event_type == "tool/call":
        projection.tool_call_count += 1
        record_pending_tool_call(projection, event)
        projection.messages.append(
            MessageProjection(
                "assistant-tool-call",
                "",
                event.call_id,
                event.name,
                event.arguments,
            )
        )
    elif event.event_type == "tool/result":
        fold_tool_result_stats(projection, event)
        projection.messages.append(
            MessageProjection("tool", event.text, event.call_id, event.name)
        )
    elif event.event_type == "todo/write":
        projection.todos = decode_todos(event.text)
    elif event.event_type == "plan/mode":
        projection.plan_mode_active = decode_plan_mode(event.text)
        projection.plan_mode = "plan" if projection.plan_mode_active else "default"
    elif event.event_type == "request/header":
        fields = split_fields(event.text, "\t")
        if len(fields) > 0:
            projection.request_provider = fields[0]
        if len(fields) > 1:
            projection.request_model = fields[1]
        if len(fields) > 2:
            projection.request_system = fields[2]
    elif event.event_type == "interaction/request":
        projection.open_interactions.append(event.call_id)
    elif event.event_type == "interaction/resolve":
        remove_text(projection.open_interactions, event.call_id)
    elif event.event_type == "turn/start":
        projection.todos = []
        projection.last_turn = event.turn
    elif event.event_type == "step/start":
        projection.step_count += 1
        projection.last_step = event.step
        projection.stats_open_turn = event.turn
        projection.stats_open_step = event.step
        projection.stats_step_started_at = event.event_time
        projection.stats_first_token_at = -1
    elif event.event_type == "step/end":
        projection.session_stats.steps += 1
        if projection.stats_last_turn != event.turn:
            projection.session_stats.turns += 1
            projection.stats_last_turn = event.turn
        projection.stats_open_turn = 0
        projection.stats_open_step = 0
        projection.stats_first_token_at = -1
    elif event.event_type == "turn/end":
        projection.completed_turn_count += 1
        if event.reason == "error":
            projection.error_turn_count += 1
        projection.last_turn_reason = event.reason
        projection.stats_pending_call_ids = []
        projection.stats_pending_call_times = []


def fold_assistant_stats(projection: SessionProjection, event: SessionEvent) -> None:
    """Close one matching model timing boundary from a logged message."""
    if (
        projection.stats_open_turn != event.turn
        or projection.stats_open_step != event.step
    ):
        return
    elapsed = event.event_time - projection.stats_step_started_at
    if elapsed < 0:
        elapsed = 0
    projection.session_stats.llm_ms += elapsed
    if projection.stats_first_token_at >= 0:
        first = projection.stats_first_token_at - projection.stats_step_started_at
        if first < 0:
            first = 0
        projection.session_stats.ttft_ms += first
        projection.session_stats.ttft_steps += 1
        if event.output_tokens >= 0:
            decode = event.event_time - projection.stats_first_token_at
            if decode < 0:
                decode = 0
            projection.session_stats.decode_ms += decode
            projection.session_stats.decode_tokens += event.output_tokens
    projection.stats_open_turn = 0
    projection.stats_open_step = 0
    projection.stats_first_token_at = -1


def fold_tool_result_stats(projection: SessionProjection, event: SessionEvent) -> None:
    """Pair one tool result with its logged dispatch time by opaque call id."""
    i = 0
    while i < len(projection.stats_pending_call_ids):
        if projection.stats_pending_call_ids[i] == event.call_id:
            elapsed = event.event_time - projection.stats_pending_call_times[i]
            if elapsed < 0:
                elapsed = 0
            projection.session_stats.tool_ms += elapsed
            projection.stats_pending_call_ids.pop(i)
            projection.stats_pending_call_times.pop(i)
            return
        i += 1


def record_pending_tool_call(projection: SessionProjection, event: SessionEvent) -> None:
    """Record or replace the dispatch boundary for one opaque call id."""
    i = 0
    while i < len(projection.stats_pending_call_ids):
        if projection.stats_pending_call_ids[i] == event.call_id:
            projection.stats_pending_call_times[i] = event.event_time
            return
        i += 1
    projection.stats_pending_call_ids.append(event.call_id)
    projection.stats_pending_call_times.append(event.event_time)


def derive_session_title(text: str) -> str:
    """Derive a stable compact title from the first logged user message."""
    title = text.replace("\n", " ").replace("\t", " ").strip()
    while "  " in title:
        title = title.replace("  ", " ")
    if title == "":
        return "New Session"
    if len(title) <= 60:
        return title
    return title[:57] + "..."


def encode_todos(todos) -> str:
    out = ""
    i = 0
    while i < len(todos):
        if i > 0:
            out += "\n"
        out += escape_field(todos[i].status) + "\t" + escape_field(todos[i].content)
        i += 1
    return out


def encode_plan_mode(active: bool) -> str:
    return "true" if active else "false"


def decode_plan_mode(text: str) -> bool:
    if text == "true" or text == "plan":
        return True
    if text == "false" or text == "default":
        return False
    raise ValueError("invalid plan mode event value")


def fold_plan_mode(events) -> bool:
    active = False
    i = 0
    while i < len(events):
        event = events[i]
        if event.event_type == "plan/mode":
            active = decode_plan_mode(event.text)
        i += 1
    return active


def decode_todos(text: str):
    todos = []
    if text == "":
        return todos
    lines = split_fields(text, "\n")
    i = 0
    while i < len(lines):
        fields = split_fields(lines[i], "\t")
        if len(fields) != 2:
            raise ValueError("invalid todo snapshot")
        todos.append(TodoItem(unescape_field(fields[1]), unescape_field(fields[0])))
        i += 1
    return todos


def escape_field(text: str) -> str:
    return text.replace("%", "%25").replace("\n", "%0A").replace("\t", "%09")


def unescape_field(text: str) -> str:
    return text.replace("%09", "\t").replace("%0A", "\n").replace("%25", "%")


def split_fields(text: str, separator: str):
    fields = []
    start = 0
    while True:
        index = text.find(separator, start)
        if index < 0:
            fields.append(text[start:])
            return fields
        fields.append(text[start:index])
        start = index + len(separator)


def remove_text(values, target: str) -> None:
    i = 0
    while i < len(values):
        if values[i] == target:
            values.pop(i)
            return
        i += 1
