from tool_runtime import (
    Cancellation,
    ToolDefinition,
    ToolOutcome,
    ToolRuntime,
    ToolSchema,
)


def runtime_with_tool(body):
    runtime = ToolRuntime()
    runtime.register(ToolDefinition(ToolSchema("sample", "sample tool"), body))
    return runtime


def test_execution_pipeline_preserves_pre_around_body_post_order():
    order = []
    runtime = runtime_with_tool(lambda arguments, execution: order.append("body") or arguments)

    def pre(execution, next_):
        order.append("pre-before")
        result = next_()
        order.append("pre-after")
        return result

    def around(execution, next_):
        order.append("around-before")
        result = next_()
        order.append("around-after")
        return result

    def post(execution, outcome, next_):
        order.append("post")
        return next_(ToolOutcome(outcome.content + "!"))

    runtime.add_pre_hook(pre)
    runtime.add_around_hook(around)
    runtime.add_post_hook(post)

    outcome = runtime.execute("call-1", "sample", "ok")

    assert outcome.content == "ok!"
    assert order == [
        "pre-before",
        "around-before",
        "body",
        "post",
        "around-after",
        "pre-after",
    ]


def test_missing_next_denies_before_body():
    invoked = []
    runtime = runtime_with_tool(lambda arguments, execution: invoked.append(True))
    runtime.add_pre_hook(lambda execution, next_: None)

    outcome = runtime.execute("call-1", "sample", "ignored")

    assert outcome.is_error
    assert outcome.error_code == "DENIED"
    assert invoked == []


def test_unknown_tool_and_body_failure_are_normalized():
    runtime = runtime_with_tool(
        lambda arguments, execution: (_ for _ in ()).throw(ValueError("bad input"))
    )

    unknown = runtime.execute("call-1", "missing", "{}")
    failed = runtime.execute("call-2", "sample", "{}")

    assert unknown.error_code == "UNKNOWN_TOOL"
    assert failed.error_name == "ValueError"
    assert failed.error_code == "TOOL_ERROR"
    assert failed.content == "bad input"


def test_cancellation_is_checked_before_tool_body():
    invoked = []
    runtime = runtime_with_tool(lambda arguments, execution: invoked.append(True))
    cancellation = Cancellation()
    cancellation.cancel("user")

    outcome = runtime.execute("call-1", "sample", "", cancellation)

    assert outcome.is_error
    assert "cancelled" in outcome.content
    assert invoked == []


def test_result_observer_receives_normalized_outcome():
    runtime = runtime_with_tool(lambda arguments, execution: "done")
    seen = []
    runtime.on_result(lambda execution, outcome: seen.append((execution.call_id, outcome.content)))

    runtime.execute("call-9", "sample", "")

    assert seen == [("call-9", "done")]
