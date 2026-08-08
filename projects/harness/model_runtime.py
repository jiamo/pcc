"""Model provider registry, deterministic provider, and DeepSeek SSE adapter."""

import json


class ModelMessage:
    """Provider-neutral model message."""

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


class ModelRequest:
    """One resolved provider request."""

    def __init__(
        self,
        provider: str,
        model: str,
        system: str,
        messages,
        tools,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> None:
        if provider == "":
            raise ValueError("model provider must not be empty")
        if model == "":
            raise ValueError("model id must not be empty")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self.provider = provider
        self.model = model
        self.system = system
        self.messages = messages
        self.tools = tools
        self.max_tokens = max_tokens
        self.temperature = temperature


class ToolCall:
    """One complete model-requested tool call."""

    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.call_id = call_id
        self.name = name
        self.arguments = arguments


class ModelChunk:
    """One replayable streamed model delta."""

    def __init__(
        self,
        kind: str,
        text: str = "",
        call_id: str = "",
        name: str = "",
        arguments: str = "",
        input_tokens: int = -1,
        output_tokens: int = -1,
    ) -> None:
        self.kind = kind
        self.text = text
        self.call_id = call_id
        self.name = name
        self.arguments = arguments
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class ModelResult:
    """Assembled result and its original stream chunks."""

    def __init__(
        self,
        text: str,
        tool_calls,
        chunks,
        stop_reason: str = "stop",
        input_tokens: int = -1,
        output_tokens: int = -1,
    ) -> None:
        if input_tokens < -1 or output_tokens < -1:
            raise ValueError("model token usage must be non-negative or absent")
        self.text = text
        self.tool_calls = tool_calls
        self.chunks = chunks
        self.stop_reason = stop_reason
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class ProviderRegistration:
    """Idempotent provider disposer."""

    def __init__(self, registry, name: str) -> None:
        self.registry = registry
        self.name = name
        self.active = True

    def dispose(self) -> None:
        if not self.active:
            return
        self.active = False
        self.registry.unregister(self.name)


class ModelProviderRegistry:
    """Validated registry of model transport providers."""

    def __init__(self) -> None:
        self.names = []
        self.providers = []

    def register(self, name: str, provider) -> ProviderRegistration:
        if name == "":
            raise ValueError("provider name must not be empty")
        if self.index_of(name) >= 0:
            raise ValueError("model provider already registered: " + name)
        self.names.append(name)
        self.providers.append(provider)
        return ProviderRegistration(self, name)

    def unregister(self, name: str) -> None:
        index = self.index_of(name)
        if index < 0:
            return
        self.names.pop(index)
        self.providers.pop(index)

    def resolve(self, name: str):
        index = self.index_of(name)
        if index < 0:
            raise KeyError("model provider is not registered: " + name)
        return self.providers[index]

    def complete(self, request: ModelRequest) -> ModelResult:
        return self.resolve(request.provider).complete(request)

    def index_of(self, name: str) -> int:
        i = 0
        while i < len(self.names):
            if self.names[i] == name:
                return i
            i += 1
        return -1


class DeterministicModelProvider:
    """Keyless provider for native tests, snapshots, and offline startup."""

    def complete(self, request: ModelRequest) -> ModelResult:
        if len(request.messages) == 0:
            return text_result("PCC Harness is ready.")
        last = request.messages[len(request.messages) - 1]
        if last.role == "tool":
            return text_result("Tool returned: " + last.content)
        prompt = last.content
        if prompt.startswith("/tool echo "):
            call = ToolCall("call-1", "echo", prompt[11:])
            chunk = ModelChunk(
                "tool-call", "", call.call_id, call.name, call.arguments
            )
            return ModelResult("", [call], [chunk], "tool_calls")
        if prompt == "/about":
            return text_result(
                "PCC-native DeepSeek Harness; session format 0"
            )
        return text_result("PCC harness is running. You said: " + prompt)


class DeepSeekSseProvider:
    """DeepSeek-compatible streaming provider over a PCC-owned HTTP transport.

    The injected transport owns DNS, TLS, proxying, pooling, cancellation, and
    virtual-thread I/O. Its ``post_sse`` method returns arbitrary text chunks;
    this provider owns request JSON and SSE/OpenAI-compatible response parsing.
    """

    def __init__(self, transport, api_key: str, base_url: str) -> None:
        if api_key == "":
            raise ValueError("DeepSeek credential reference resolved empty")
        if base_url == "":
            raise ValueError("DeepSeek base URL must not be empty")
        self.transport = transport
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def complete(self, request: ModelRequest) -> ModelResult:
        body = encode_request(request)
        headers = [
            ["authorization", "Bearer " + self.api_key],
            ["content-type", "application/json"],
            ["accept", "text/event-stream"],
        ]
        pieces = self.transport.post_sse(
            self.base_url + "/chat/completions", headers, body
        )
        decoder = SseDecoder()
        chunks = []
        i = 0
        while i < len(pieces):
            payloads = decoder.feed(pieces[i])
            append_payload_chunks(payloads, chunks)
            i += 1
        payloads = decoder.finish()
        append_payload_chunks(payloads, chunks)
        return assemble_chunks(chunks)


class SseDecoder:
    """Incremental SSE decoder tolerant of transport chunk boundaries."""

    def __init__(self) -> None:
        self.buffer = ""
        self.data_lines = []

    def feed(self, text: str):
        self.buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        payloads = []
        while True:
            index = self.buffer.find("\n")
            if index < 0:
                return payloads
            line = self.buffer[:index]
            self.buffer = self.buffer[index + 1 :]
            self._accept_line(line, payloads)

    def finish(self):
        payloads = []
        if self.buffer != "":
            self._accept_line(self.buffer, payloads)
            self.buffer = ""
        self._flush(payloads)
        return payloads

    def _accept_line(self, line: str, payloads) -> None:
        if line == "":
            self._flush(payloads)
        elif line.startswith("data:"):
            data = line[5:]
            if data.startswith(" "):
                data = data[1:]
            self.data_lines.append(data)

    def _flush(self, payloads) -> None:
        if len(self.data_lines) == 0:
            return
        payloads.append("\n".join(self.data_lines))
        self.data_lines = []


def encode_request(request: ModelRequest) -> str:
    messages = []
    if request.system != "":
        messages.append({"role": "system", "content": request.system})
    i = 0
    while i < len(request.messages):
        message = request.messages[i]
        record = {"role": message.role, "content": message.content}
        if message.call_id != "":
            record["tool_call_id"] = message.call_id
        if message.name != "":
            record["name"] = message.name
        messages.append(record)
        i += 1
    tools = []
    i = 0
    while i < len(request.tools):
        tool = request.tools[i]
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
        i += 1
    body = {
        "model": request.model,
        "messages": messages,
        "stream": True,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
    }
    if len(tools) > 0:
        body["tools"] = tools
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def append_payload_chunks(payloads, chunks) -> None:
    i = 0
    while i < len(payloads):
        payload = payloads[i]
        if payload != "[DONE]":
            decode_deepseek_payload(payload, chunks)
        i += 1


def decode_deepseek_payload(payload: str, chunks) -> None:
    try:
        record = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError("invalid DeepSeek SSE JSON: " + str(error)) from error
    usage = record.get("usage")
    if isinstance(usage, dict):
        input_tokens = usage.get("prompt_tokens", -1)
        output_tokens = usage.get("completion_tokens", -1)
        if (
            isinstance(input_tokens, int)
            and not isinstance(input_tokens, bool)
            and input_tokens >= 0
            and isinstance(output_tokens, int)
            and not isinstance(output_tokens, bool)
            and output_tokens >= 0
        ):
            chunks.append(
                ModelChunk(
                    "usage",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            )
    choices = record.get("choices", [])
    if len(choices) == 0:
        return
    delta = choices[0].get("delta", {})
    content = delta.get("content")
    if content is not None and content != "":
        chunks.append(ModelChunk("text", str(content)))
    tool_calls = delta.get("tool_calls", [])
    i = 0
    while i < len(tool_calls):
        call = tool_calls[i]
        function = call.get("function", {})
        chunks.append(
            ModelChunk(
                "tool-call-delta",
                "",
                str(call.get("id", "")),
                str(function.get("name", "")),
                str(function.get("arguments", "")),
            )
        )
        i += 1


def assemble_chunks(chunks) -> ModelResult:
    text = ""
    tool_calls = []
    input_tokens = -1
    output_tokens = -1
    i = 0
    while i < len(chunks):
        chunk = chunks[i]
        if chunk.kind == "text":
            text += chunk.text
        elif chunk.kind == "tool-call" or chunk.kind == "tool-call-delta":
            merge_tool_delta(tool_calls, chunk)
        elif chunk.kind == "usage":
            input_tokens = chunk.input_tokens
            output_tokens = chunk.output_tokens
        i += 1
    reason = "tool_calls" if len(tool_calls) > 0 else "stop"
    return ModelResult(
        text,
        tool_calls,
        chunks,
        reason,
        input_tokens,
        output_tokens,
    )


def merge_tool_delta(tool_calls, chunk: ModelChunk) -> None:
    if len(tool_calls) == 0 or (
        chunk.call_id != "" and tool_calls[len(tool_calls) - 1].call_id != chunk.call_id
    ):
        tool_calls.append(
            ToolCall(chunk.call_id, chunk.name, chunk.arguments)
        )
        return
    call = tool_calls[len(tool_calls) - 1]
    if chunk.call_id != "":
        call.call_id = chunk.call_id
    call.name += chunk.name
    call.arguments += chunk.arguments


def text_result(text: str) -> ModelResult:
    return ModelResult(text, [], [ModelChunk("text", text)])
