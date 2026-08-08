"""Internal channel pipeline for transport stages.

This borrows Netty's useful separation between inbound and outbound stages but
does not expose callbacks, futures or event-loop ownership to ``pcc.web``
applications. A connection virtual thread invokes the pipeline sequentially.
"""

INBOUND = 1
OUTBOUND = 2
BOTH = 3


class ChannelClosedError(RuntimeError):
    pass


class PipelineError(RuntimeError):
    pass


class ChannelContext:
    def __init__(self, connection_id: int, generation, cancellation) -> None:
        self.connection_id = connection_id
        self.generation = generation
        self.cancellation = cancellation
        self.attributes = {}
        self.closed = False
        self.error = None

    def close(self, error=None) -> None:
        self.closed = True
        self.error = error
        if self.cancellation is not None:
            self.cancellation.cancel("channel closed")


class ChannelStage:
    """A fixed pipeline stage; override only the required direction."""

    def __init__(self, name: str, direction: int = BOTH) -> None:
        if not name or direction not in (INBOUND, OUTBOUND, BOTH):
            raise ValueError("invalid channel stage")
        self.name = name
        self.direction = direction

    def inbound(self, context: ChannelContext, message):
        return message

    def outbound(self, context: ChannelContext, message):
        return message

    def close(self, context: ChannelContext) -> None:
        pass


class ChannelPipeline:
    """Immutable ordered stages with deterministic error and close behavior."""

    def __init__(self, stages=()) -> None:
        self.stages = tuple(stages)
        names = set()
        for stage in self.stages:
            if stage.name in names:
                raise ValueError("duplicate channel stage name: " + stage.name)
            names.add(stage.name)

    def inbound(self, context: ChannelContext, message):
        if context.closed:
            raise ChannelClosedError("inbound message on closed channel")
        current = message
        try:
            for stage in self.stages:
                if stage.direction & INBOUND:
                    current = stage.inbound(context, current)
                    if current is None:
                        break
        except Exception as error:
            context.close(error)
            raise PipelineError("inbound stage failed") from error
        return current

    def outbound(self, context: ChannelContext, message):
        if context.closed:
            raise ChannelClosedError("outbound message on closed channel")
        current = message
        try:
            index = len(self.stages) - 1
            while index >= 0:
                stage = self.stages[index]
                if stage.direction & OUTBOUND:
                    current = stage.outbound(context, current)
                    if current is None:
                        break
                index -= 1
        except Exception as error:
            context.close(error)
            raise PipelineError("outbound stage failed") from error
        return current

    def close(self, context: ChannelContext) -> None:
        if context.closed:
            return
        context.closed = True
        first_error = None
        index = len(self.stages) - 1
        while index >= 0:
            try:
                self.stages[index].close(context)
            except Exception as error:
                if first_error is None:
                    first_error = error
            index -= 1
        if context.cancellation is not None:
            context.cancellation.cancel("channel closed")
        if first_error is not None:
            context.error = first_error


class ByteLimitStage(ChannelStage):
    def __init__(self, name: str, max_inbound: int, max_outbound: int) -> None:
        super().__init__(name)
        self.max_inbound = max_inbound
        self.max_outbound = max_outbound

    def inbound(self, context: ChannelContext, message):
        count = context.attributes.get(self.name + ".in", 0) + len(message)
        if count > self.max_inbound:
            raise ValueError("channel inbound byte limit exceeded")
        context.attributes[self.name + ".in"] = count
        return message

    def outbound(self, context: ChannelContext, message):
        count = context.attributes.get(self.name + ".out", 0) + len(message)
        if count > self.max_outbound:
            raise ValueError("channel outbound byte limit exceeded")
        context.attributes[self.name + ".out"] = count
        return message
