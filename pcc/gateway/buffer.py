"""Owned segmented buffers and byte-accurate gateway backpressure.

``BufferSegment`` is the allocation owner. ``BufferView`` retains that owner,
so a parser or handler may keep a slice across a virtual-thread park without
leaving a borrowed raw pointer into relocatable managed storage.  The current
implementation intentionally uses managed ``bytearray`` storage; native pool
specialization can replace the allocator without changing these lifetimes.

``ChannelBuffer`` never blocks a carrier.  It reports high/low watermark
transitions to the socket layer, which parks/wakes the producing virtual thread.
"""

BACKPRESSURE_NONE = 0
BACKPRESSURE_HIGH = 1
BACKPRESSURE_LOW = 2


class BufferReleasedError(RuntimeError):
    pass


class BufferClosedError(RuntimeError):
    pass


class BufferLimitError(RuntimeError):
    pass


class BufferSegment:
    """One reference-counted, fixed-capacity storage owner."""

    def __init__(self, capacity: int = 16384) -> None:
        if capacity <= 0:
            raise ValueError("buffer segment capacity must be positive")
        self.capacity = capacity
        self.data = bytearray(b"\x00" * capacity)
        self.start = 0
        self.end = 0
        self.references = 1
        self.released = False

    def retain(self):
        if self.released:
            raise BufferReleasedError("cannot retain a released segment")
        self.references += 1
        return self

    def release(self) -> int:
        if self.released:
            raise BufferReleasedError("segment released more than once")
        self.references -= 1
        if self.references < 0:
            raise BufferReleasedError("negative segment reference count")
        if self.references == 0:
            self.released = True
            self.data = bytearray(b"")
            self.start = 0
            self.end = 0
            return 1
        return 0

    def readable_bytes(self) -> int:
        if self.released:
            return 0
        return self.end - self.start

    def writable_bytes(self) -> int:
        if self.released:
            return 0
        return self.capacity - self.end

    def write(self, source, offset: int = 0, length: int = -1) -> int:
        if self.released:
            raise BufferReleasedError("cannot write a released segment")
        source_length = len(source)
        if offset < 0 or offset > source_length:
            raise ValueError("source offset is out of range")
        if length < 0:
            length = source_length - offset
        if length < 0 or offset + length > source_length:
            raise ValueError("source length is out of range")
        count = length
        available = self.writable_bytes()
        if count > available:
            count = available
        index = 0
        while index < count:
            self.data[self.end + index] = source[offset + index]
            index += 1
        self.end += count
        return count

    def consume(self, length: int) -> int:
        if self.released:
            raise BufferReleasedError("cannot consume a released segment")
        if length < 0:
            raise ValueError("consume length must not be negative")
        count = length
        readable = self.readable_bytes()
        if count > readable:
            count = readable
        self.start += count
        return count

    def view(self, offset: int = 0, length: int = -1):
        if self.released:
            raise BufferReleasedError("cannot view a released segment")
        readable = self.readable_bytes()
        if offset < 0 or offset > readable:
            raise ValueError("view offset is out of range")
        if length < 0:
            length = readable - offset
        if length < 0 or offset + length > readable:
            raise ValueError("view length is out of range")
        return BufferView(self, self.start + offset, length)


class BufferView:
    """A retained immutable range within a ``BufferSegment``."""

    def __init__(self, owner: BufferSegment, start: int, length: int) -> None:
        if owner.released:
            raise BufferReleasedError("cannot create a view of released storage")
        if start < 0 or length < 0 or start + length > owner.end:
            raise ValueError("buffer view range is invalid")
        self.owner = owner.retain()
        self.start = start
        self.length = length
        self.released = False

    def __len__(self) -> int:
        if self.released:
            return 0
        return self.length

    def to_bytes(self) -> bytes:
        if self.released:
            raise BufferReleasedError("cannot read a released view")
        return bytes(self.owner.data[self.start:self.start + self.length])

    def slice(self, offset: int = 0, length: int = -1):
        if self.released:
            raise BufferReleasedError("cannot slice a released view")
        if offset < 0 or offset > self.length:
            raise ValueError("view slice offset is out of range")
        if length < 0:
            length = self.length - offset
        if length < 0 or offset + length > self.length:
            raise ValueError("view slice length is out of range")
        return BufferView(self.owner, self.start + offset, length)

    def release(self) -> int:
        if self.released:
            raise BufferReleasedError("buffer view released more than once")
        self.released = True
        self.length = 0
        return self.owner.release()


class ChannelBuffer:
    """A bounded FIFO of owned views with high/low watermark transitions."""

    def __init__(
        self,
        segment_size: int = 16384,
        low_watermark: int = 32768,
        high_watermark: int = 65536,
        max_bytes: int = 1048576,
    ) -> None:
        if segment_size <= 0:
            raise ValueError("segment size must be positive")
        if low_watermark < 0 or high_watermark <= low_watermark:
            raise ValueError("watermarks must satisfy 0 <= low < high")
        if max_bytes < high_watermark:
            raise ValueError("max bytes must cover the high watermark")
        self.segment_size = segment_size
        self.low_watermark = low_watermark
        self.high_watermark = high_watermark
        self.max_bytes = max_bytes
        self.views = []
        self.head = 0
        self.head_offset = 0
        self.queued_bytes = 0
        self.backpressured = False
        self.closed = False

    def __len__(self) -> int:
        return self.queued_bytes

    def _transition_after_growth(self) -> int:
        if not self.backpressured and self.queued_bytes >= self.high_watermark:
            self.backpressured = True
            return BACKPRESSURE_HIGH
        return BACKPRESSURE_NONE

    def _transition_after_drain(self) -> int:
        if self.backpressured and self.queued_bytes <= self.low_watermark:
            self.backpressured = False
            return BACKPRESSURE_LOW
        return BACKPRESSURE_NONE

    def append(self, source) -> int:
        """Copy bytes into owned segments and return a watermark transition."""
        if self.closed:
            raise BufferClosedError("cannot append to a closed channel buffer")
        source_length = len(source)
        if self.queued_bytes + source_length > self.max_bytes:
            raise BufferLimitError("channel buffer byte limit exceeded")
        offset = 0
        while offset < source_length:
            capacity = self.segment_size
            remaining = source_length - offset
            if remaining < capacity:
                capacity = remaining
            segment = BufferSegment(capacity)
            written = segment.write(source, offset, remaining)
            view = segment.view(0, written)
            segment.release()
            self.views.append(view)
            offset += written
        self.queued_bytes += source_length
        return self._transition_after_growth()

    def append_view(self, view: BufferView) -> int:
        """Retain an existing view without copying it."""
        if self.closed:
            raise BufferClosedError("cannot append to a closed channel buffer")
        length = len(view)
        if self.queued_bytes + length > self.max_bytes:
            raise BufferLimitError("channel buffer byte limit exceeded")
        self.views.append(view.slice())
        self.queued_bytes += length
        return self._transition_after_growth()

    def peek_views(self, limit: int = -1):
        """Return retained views covering at most ``limit`` queued bytes."""
        result = []
        remaining = self.queued_bytes
        if limit >= 0 and limit < remaining:
            remaining = limit
        index = self.head
        local_offset = self.head_offset
        while index < len(self.views) and remaining > 0:
            view = self.views[index]
            available = len(view) - local_offset
            take = available
            if take > remaining:
                take = remaining
            result.append(view.slice(local_offset, take))
            remaining -= take
            index += 1
            local_offset = 0
        return result

    def consume(self, length: int) -> int:
        """Release consumed owners and return a low-watermark transition."""
        if length < 0 or length > self.queued_bytes:
            raise ValueError("consume length exceeds queued bytes")
        remaining = length
        while remaining > 0:
            view = self.views[self.head]
            available = len(view) - self.head_offset
            if remaining < available:
                self.head_offset += remaining
                remaining = 0
            else:
                remaining -= available
                view.release()
                self.head += 1
                self.head_offset = 0
        self.queued_bytes -= length
        if self.head > 32 and self.head * 2 >= len(self.views):
            self.views = self.views[self.head:]
            self.head = 0
        return self._transition_after_drain()

    def read(self, limit: int = -1) -> bytes:
        if limit < 0 or limit > self.queued_bytes:
            limit = self.queued_bytes
        parts = self.peek_views(limit)
        output = bytearray(b"")
        for part in parts:
            output.extend(part.to_bytes())
            part.release()
        self.consume(limit)
        return bytes(output)

    def close(self) -> int:
        if self.closed:
            return 0
        released = 0
        index = self.head
        while index < len(self.views):
            view = self.views[index]
            if not view.released:
                view.release()
                released += 1
            index += 1
        self.views = []
        self.head = 0
        self.head_offset = 0
        self.queued_bytes = 0
        self.backpressured = False
        self.closed = True
        return released
