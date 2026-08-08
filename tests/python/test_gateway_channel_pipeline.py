from pcc.gateway.channel import (
    INBOUND,
    OUTBOUND,
    ChannelContext,
    ChannelPipeline,
    ChannelStage,
    PipelineError,
)
from pcc.web.models import Cancellation


class RecordStage(ChannelStage):
    def __init__(self, name, log, direction=3):
        super().__init__(name, direction)
        self.log = log

    def inbound(self, context, message):
        self.log.append(self.name + ":in")
        return message + self.name.encode()

    def outbound(self, context, message):
        self.log.append(self.name + ":out")
        return message + self.name.encode()

    def close(self, context):
        self.log.append(self.name + ":close")


def test_pipeline_direction_and_order_are_internal_and_deterministic() -> None:
    log = []
    pipeline = ChannelPipeline((
        RecordStage("a", log, INBOUND),
        RecordStage("b", log),
        RecordStage("c", log, OUTBOUND),
    ))
    context = ChannelContext(1, None, Cancellation())
    assert pipeline.inbound(context, b"") == b"ab"
    assert pipeline.outbound(context, b"") == b"cb"
    assert log == ["a:in", "b:in", "c:out", "b:out"]
    pipeline.close(context)
    assert log[-3:] == ["c:close", "b:close", "a:close"]
    assert context.cancellation.cancelled


class BrokenStage(ChannelStage):
    def inbound(self, context, message):
        raise ValueError("secret")


def test_stage_failure_closes_scope_and_wraps_internal_error() -> None:
    cancellation = Cancellation()
    context = ChannelContext(1, None, cancellation)
    try:
        ChannelPipeline((BrokenStage("bad"),)).inbound(context, b"x")
    except PipelineError:
        assert context.closed
        assert cancellation.cancelled
        assert isinstance(context.error, ValueError)
        return
    raise AssertionError("pipeline error was swallowed")
