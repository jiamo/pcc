import json

import pytest

from model_runtime import (
    DeepSeekSseProvider,
    DeterministicModelProvider,
    ModelMessage,
    ModelProviderRegistry,
    ModelRequest,
    SseDecoder,
)


class FakeTransport:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def post_sse(self, url, headers, body):
        self.calls.append((url, headers, body))
        return self.chunks


def request(messages=None):
    return ModelRequest(
        "deepseek", "deepseek-chat", "system", messages or [], [], 100
    )


def test_provider_registration_is_duplicate_safe_and_reversible():
    registry = ModelProviderRegistry()
    registration = registry.register("local", DeterministicModelProvider())
    with pytest.raises(ValueError, match="already registered"):
        registry.register("local", DeterministicModelProvider())
    registration.dispose()
    with pytest.raises(KeyError, match="not registered"):
        registry.resolve("local")


def test_sse_decoder_handles_arbitrary_chunk_boundaries_and_multiline_data():
    decoder = SseDecoder()
    assert decoder.feed("data: one\ndata:") == []
    assert decoder.feed(" two\n\n") == ["one\ntwo"]
    assert decoder.feed("data: final") == []
    assert decoder.finish() == ["final"]


def test_deepseek_provider_assembles_text_stream_and_request():
    transport = FakeTransport(
        [
            'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n',
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
            "data: [DONE]\n\n",
        ]
    )
    provider = DeepSeekSseProvider(transport, "secret", "https://api.deepseek.com/")

    result = provider.complete(request([ModelMessage("user", "say hello")]))

    assert result.text == "hello"
    assert [chunk.text for chunk in result.chunks] == ["hel", "lo"]
    url, headers, body = transport.calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert ["authorization", "Bearer secret"] in headers
    decoded = json.loads(body)
    assert decoded["model"] == "deepseek-chat"
    assert decoded["stream"] is True
    assert decoded["messages"][-1] == {"role": "user", "content": "say hello"}


def test_deepseek_provider_preserves_usage_from_choice_less_final_payload():
    transport = FakeTransport(
        [
            'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n',
            'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":11}}\n\n',
            "data: [DONE]\n\n",
        ]
    )
    provider = DeepSeekSseProvider(transport, "secret", "https://api.deepseek.com")

    result = provider.complete(request())

    assert result.text == "answer"
    assert result.input_tokens == 7
    assert result.output_tokens == 11
    assert [chunk.kind for chunk in result.chunks] == ["text", "usage"]


def test_deepseek_provider_merges_streamed_tool_call_deltas():
    transport = FakeTransport(
        [
            'data: {"choices":[{"delta":{"tool_calls":[{"id":"call-7","function":{"name":"ec","arguments":"nat"}}]}}]}\n\n',
            'data: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"ho","arguments":"ive"}}]}}]}\n\n',
            "data: [DONE]\n\n",
        ]
    )
    provider = DeepSeekSseProvider(transport, "secret", "https://api.deepseek.com")

    result = provider.complete(request([ModelMessage("user", "use tool")]))

    assert result.stop_reason == "tool_calls"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].call_id == "call-7"
    assert result.tool_calls[0].name == "echo"
    assert result.tool_calls[0].arguments == "native"


def test_invalid_sse_json_fails_loudly():
    provider = DeepSeekSseProvider(
        FakeTransport(["data: not-json\n\n"]),
        "secret",
        "https://api.deepseek.com",
    )
    with pytest.raises(ValueError, match="invalid DeepSeek SSE JSON"):
        provider.complete(request())
