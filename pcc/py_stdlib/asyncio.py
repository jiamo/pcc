"""pcc.py_stdlib.asyncio -- minimal native asyncio surface.

This module intentionally covers the object model used by pcc's
no-libpython path: coroutine driving, futures/tasks, events, queues,
streams, and protocol/transport base classes. The TCP stream support is
blocking and deliberately small; it exists so native no-libpython programs can
serve simple stream workloads without falling back to CPython.
"""
from __future__ import annotations

from pcc.extern import extern, c_int64, c_ptr, c_obj
from pcc.unsafe import is_tagged_int, load_i32, ptr_is_null


_py_await: "extern" = extern("py_await", (c_ptr,), c_obj)
_py_asyncio_sleep: "extern" = extern("py_asyncio_sleep", (c_ptr,), c_obj)
_py_coroutine_args: "extern" = extern("py_coroutine_get_args", (c_ptr,), c_obj)
_py_task_new: "extern" = extern("py_task_new", (c_ptr,), c_obj)
_py_task_step: "extern" = extern("py_task_step", (c_ptr,), c_obj)
_tcp_listen: "extern" = extern("py_asyncio_tcp_listen", (c_ptr, c_ptr, c_int64), c_obj)
_tcp_accept: "extern" = extern("py_asyncio_tcp_accept", (c_ptr,), c_obj)
_tcp_connect: "extern" = extern("py_asyncio_tcp_connect", (c_ptr, c_ptr), c_obj)
_fd_recv: "extern" = extern("py_asyncio_fd_recv", (c_ptr, c_int64), c_obj)
_fd_send_all: "extern" = extern("py_asyncio_fd_send_all", (c_ptr, c_ptr), c_int64)
_fd_relay: "extern" = extern("py_asyncio_fd_relay", (c_ptr, c_ptr, c_ptr, c_ptr), c_int64)
_fd_relay_step: "extern" = extern("py_asyncio_fd_relay_step", (c_ptr, c_ptr, c_ptr, c_ptr, c_ptr), c_obj)
_fd_relay_step_last_progress: "extern" = extern("py_asyncio_fd_relay_step_last_progress", (), c_obj)
_fd_close: "extern" = extern("py_asyncio_fd_close", (c_ptr,), c_int64)
_fd_sockname: "extern" = extern("py_asyncio_fd_sockname", (c_ptr,), c_obj)
_fd_peername: "extern" = extern("py_asyncio_fd_peername", (c_ptr,), c_obj)
_io_waitset_backend: "extern" = extern(
    "py_asyncio_io_waitset_backend", (), c_obj
)
_usleep: "extern" = extern("usleep", (c_int64,), c_int64)


class CancelledError(Exception):
    pass


class TimeoutError(Exception):
    pass


class IncompleteReadError(EOFError):
    def __init__(self, partial=None, expected=None) -> None:
        super().__init__("incomplete read")
        self.partial = partial
        self.expected = expected


class LimitOverrunError(Exception):
    def __init__(self, message="", consumed=0) -> None:
        super().__init__(message)
        self.consumed = consumed


_TASKS = []
_LOOP_BOX = [None]
_SERVERS = []
_PENDING_STREAM_RELAYS = []
# Active relays driven cooperatively by the event loop. Each entry is a mutable
# list [task_a, task_b, fd1_in, fd1_out, fd2_in, fd2_out, active_mask]; the mask
# is updated in place each step and the entry is dropped when it reaches 0.
_ACTIVE_RELAYS = []


def _is_none(value):
    if ptr_is_null(value):
        return True
    if is_tagged_int(value):
        return False
    return load_i32(value, 8) == 0


def _bytes_find(data, needle):
    needle_len = len(needle)
    if needle_len == 0:
        return 0
    data_len = len(data)
    if needle_len > data_len:
        return -1
    last = data_len - needle_len
    i = 0
    while i <= last:
        if data[i] == needle[0]:
            same = True
            j = 1
            while j < needle_len:
                if data[i + j] != needle[j]:
                    same = False
                    break
                j += 1
            if same:
                return i
        i += 1
    return -1


class Future:
    def __init__(self) -> None:
        self._done = False
        self._cancelled = False
        self._result = None
        self._exception = None
        self._callbacks = []

    def done(self):
        return self._done

    def cancelled(self):
        return self._cancelled

    def cancel(self):
        if self._done:
            return False
        self._cancelled = True
        self._done = True
        self._run_callbacks()
        return True

    def set_result(self, value):
        self._result = value
        self._done = True
        self._run_callbacks()

    def set_exception(self, exc):
        self._exception = exc
        self._done = True
        self._run_callbacks()

    def result(self):
        if self._exception is not None:
            raise self._exception
        if self._cancelled:
            raise CancelledError()
        return self._result

    def exception(self):
        return self._exception

    def add_done_callback(self, fn, *args, **kwargs):
        if self._done:
            fn(self)
        else:
            self._callbacks.append(fn)

    def remove_done_callback(self, fn):
        kept = []
        removed = 0
        for cb in self._callbacks:
            if cb is fn:
                removed += 1
            else:
                kept.append(cb)
        self._callbacks = kept
        return removed

    def _run_callbacks(self):
        callbacks = self._callbacks
        self._callbacks = []
        for cb in callbacks:
            cb(self)

    def __await__(self):
        yield
        return self.result()


def _completed_future(value=None):
    future = Future()
    future.set_result(value)
    return future


class Task:
    def __init__(self, coro=None) -> None:
        self._task = _py_task_new(coro)
        self._cancelled = False
        self._done = False
        self._result = None
        self._callbacks = []

    def done(self):
        if self._cancelled:
            return True
        return self._done

    def cancelled(self):
        return self._cancelled

    def cancel(self):
        if self._done:
            return False
        self._cancelled = True
        self._done = True
        self._run_callbacks()
        return True

    def result(self):
        if self._cancelled:
            raise CancelledError()
        if not self._done:
            return self._step()
        return self._result

    def exception(self):
        return None

    def add_done_callback(self, fn, *args, **kwargs):
        if self.done():
            fn(self)
        else:
            self._callbacks.append(fn)

    def remove_done_callback(self, fn):
        kept = []
        removed = 0
        for cb in self._callbacks:
            if cb is fn:
                removed += 1
            else:
                kept.append(cb)
        self._callbacks = kept
        return removed

    def _run_callbacks(self):
        callbacks = self._callbacks
        self._callbacks = []
        for cb in callbacks:
            cb(self)

    def _step(self):
        if self._cancelled:
            return None
        if self._done:
            return self._result
        self._result = _py_task_step(self._task)
        self._done = True
        self._run_callbacks()
        return self._result

    def __await__(self):
        yield
        return self._step()

    @staticmethod
    def all_tasks(loop=None):
        return all_tasks(loop)


class Event:
    def __init__(self) -> None:
        self._flag = False

    def is_set(self):
        return self._flag

    def set(self):
        self._flag = True

    def clear(self):
        self._flag = False

    def wait(self):
        return _completed_future(self._flag)


class Queue:
    def __init__(self, maxsize=0) -> None:
        self._items = []
        self._maxsize = maxsize
        self._read_index = 0

    def qsize(self):
        return len(self._items) - self._read_index

    def empty(self):
        return self.qsize() == 0

    def full(self):
        return self._maxsize > 0 and self.qsize() >= self._maxsize

    def put_nowait(self, item):
        self._items.append(item)

    def put(self, item):
        self.put_nowait(item)
        return _completed_future(None)

    def get_nowait(self):
        if self._read_index < len(self._items):
            item = self._items[self._read_index]
            self._read_index += 1
            return item
        return None

    def get(self):
        return _completed_future(self.get_nowait())

    def task_done(self):
        return None

    def join(self):
        return _completed_future(None)


class _ByteBuffer:
    def __init__(self) -> None:
        self._data = b""

    def __len__(self):
        return len(self._data)

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        if len(self._data) == 0:
            self._data = value
        else:
            self._data = value + self._data

    def extend(self, data):
        if len(self._data) == 0:
            self._data = data
        else:
            self._data = self._data + data

    def prepend(self, data):
        if not data:
            return None
        if len(self._data) == 0:
            self._data = data
        else:
            self._data = data + self._data
        return None

    def take(self, n):
        if n is None or n < 0 or n >= len(self._data):
            data = self._data
            self._data = b""
            return data
        data = self._data[:n]
        self._data = self._data[n:]
        return data

    def take_until(self, sep):
        idx = _bytes_find(self._data, sep)
        if idx < 0:
            return self.take(-1)
        end = idx + len(sep)
        return self.take(end)


class StreamReader:
    def __init__(self, *args, **kwargs) -> None:
        self._buffer = _ByteBuffer()
        self._eof = False
        self._transport = None
        self._exception = None

    def set_transport(self, transport):
        self._transport = transport

    def feed_data(self, data):
        self._buffer.extend(data)

    def feed_eof(self):
        self._eof = True

    def set_exception(self, exc):
        self._exception = exc

    def exception(self):
        return self._exception

    def at_eof(self):
        return self._eof and len(self._buffer) == 0

    def _fd(self):
        if self._transport is None:
            return None
        return self._transport._fd

    def _fill_once(self, n=65536):
        fd = self._fd()
        if _is_none(fd) or self._eof:
            return b""
        if n is None or n <= 0:
            n = 65536
        data = _fd_recv(fd, n)
        if len(data) == 0:
            self._eof = True
        else:
            self._buffer.extend(data)
        return data

    def read(self, n=-1):
        if self._exception is not None:
            raise self._exception
        if len(self._buffer) == 0 and not self._eof:
            size = n
            if size is None or size <= 0:
                size = 65536
            self._fill_once(size)
        return _completed_future(self._buffer.take(n))

    def readexactly(self, n):
        if self._exception is not None:
            raise self._exception
        while len(self._buffer) < n and not self._eof:
            self._fill_once(n - len(self._buffer))
        data = self._buffer.take(n)
        if len(data) < n:
            raise IncompleteReadError(data, n)
        return _completed_future(data)

    def read_n(self, n):
        return self.readexactly(n)

    def read_w(self, n):
        if self._exception is not None:
            raise self._exception
        while len(self._buffer) < n and not self._eof:
            data = self._fill_once(n - len(self._buffer))
            if len(data) == 0:
                break
        return _completed_future(self._buffer.take(n))

    def rollback(self, data):
        self._buffer.prepend(data)
        return None

    def readuntil(self, separator=b"\n"):
        if self._exception is not None:
            raise self._exception
        while _bytes_find(self._buffer._data, separator) < 0 and not self._eof:
            self._fill_once(4096)
        return _completed_future(self._buffer.take_until(separator))

    def read_until(self, separator=b"\n"):
        return self.readuntil(separator)

    def readline(self):
        return self.readuntil(b"\n")


class Transport:
    def __init__(self, extra=None) -> None:
        self._extra = extra or {}
        self._closed = False
        self._protocol = None

    def is_closing(self):
        return self._closed

    def close(self):
        self._closed = True

    def abort(self):
        self.close()

    def write(self, data):
        return None

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def write_eof(self):
        return None

    def can_write_eof(self):
        return False

    def get_extra_info(self, name, default=None):
        return self._extra.get(name, default)

    def set_protocol(self, protocol):
        self._protocol = protocol

    def get_protocol(self):
        return self._protocol

    def set_write_buffer_limits(self, high=None, low=None):
        return None

    def get_write_buffer_size(self):
        return 0

    def pause_reading(self):
        return None

    def resume_reading(self):
        return None


class _Socket:
    def __init__(self, fd) -> None:
        self._fd = fd
        self._closed = False
        self.family = "AddressFamily.AF_INET"

    def fileno(self):
        return self._fd

    def getsockname(self):
        return _fd_sockname(self._fd)

    def getpeername(self):
        return _fd_peername(self._fd)

    def getsockopt(self, level, option, buflen=None):
        return b""

    def close(self):
        if not self._closed:
            self._closed = True
            _fd_close(self._fd)


class _SocketTransport(Transport):
    def __init__(self, fd, sock) -> None:
        super().__init__(
            {
                "socket": sock,
                "sockname": sock.getsockname(),
                "peername": sock.getpeername(),
            }
        )
        self._fd = fd
        self._socket = sock

    def close(self):
        if not self._closed:
            self._closed = True
            self._socket.close()

    def write(self, data):
        if self._closed:
            return None
        _fd_send_all(self._fd, data)
        return None


class StreamWriter:
    def __init__(self, transport=None, protocol=None, reader=None, loop=None) -> None:
        self._transport = transport or Transport()
        self._protocol = protocol
        self._reader = reader
        self._loop = loop

    def write(self, data):
        return self._transport.write(data)

    def writelines(self, data):
        return self._transport.writelines(data)

    def write_eof(self):
        return self._transport.write_eof()

    def can_write_eof(self):
        return self._transport.can_write_eof()

    def get_extra_info(self, name, default=None):
        return self._transport.get_extra_info(name, default)

    def is_closing(self):
        return self._transport.is_closing()

    def close(self):
        return self._transport.close()

    def drain(self):
        return _completed_future(None)

    def wait_closed(self):
        return _completed_future(None)


def _stream_relay_endpoint(awaitable):
    if ptr_is_null(awaitable) or is_tagged_int(awaitable):
        return None
    if load_i32(awaitable, 8) != 20:
        return None
    args = _py_coroutine_args(awaitable)
    if _is_none(args):
        return None
    i = 0
    n = len(args)
    while i + 1 < n:
        reader = args[i]
        writer = args[i + 1]
        if isinstance(reader, StreamReader) and isinstance(writer, StreamWriter):
            transport = writer._transport
            if isinstance(transport, _SocketTransport):
                read_fd = reader._fd()
                write_fd = transport._fd
                if not _is_none(read_fd) and not _is_none(write_fd):
                    return reader, writer, read_fd, write_fd
        i += 1
    return None


def _mark_task_done(task, result=None):
    task._result = result
    task._done = True


def _try_stream_relay(awaitable, task):
    endpoint = _stream_relay_endpoint(awaitable)
    if endpoint is None:
        return False
    i = 0
    while i < len(_PENDING_STREAM_RELAYS):
        pending_task, pending_endpoint = _PENDING_STREAM_RELAYS[i]
        if pending_task.done():
            _PENDING_STREAM_RELAYS.pop(i)
            continue
        if pending_endpoint[2] == endpoint[3] and endpoint[2] == pending_endpoint[3]:
            _PENDING_STREAM_RELAYS.pop(i)
            # Register the relay for cooperative, non-blocking driving by the
            # event loop instead of taking over the single thread with a
            # blocking full-lifetime relay. The two short-circuited channel
            # tasks are marked done immediately (the loop never has to run their
            # Python bodies); the actual byte forwarding now happens in
            # _drive_relays(), so one open/idle connection no longer freezes
            # every other connection on the loop.
            _ACTIVE_RELAYS.append(
                [
                    pending_task,
                    task,
                    pending_endpoint[2],
                    pending_endpoint[3],
                    endpoint[2],
                    endpoint[3],
                    3,
                ]
            )
            _mark_task_done(pending_task)
            _mark_task_done(task)
            return True
        i += 1
    _PENDING_STREAM_RELAYS.append((task, endpoint))
    return False


def _drive_relays():
    # One non-blocking forwarding pass over every active relay. The native step
    # returns None once a relay is finished (its fds are already closed); while a
    # relay is still open it returns an updated mask (a small Python int) that we
    # carry back into the next step. Finished relays mark their two short-circuit
    # tasks done and are dropped. Returns True when a relay finished so the loop
    # knows it made progress this pass.
    progressed = False
    i = 0
    while i < len(_ACTIVE_RELAYS):
        relay = _ACTIVE_RELAYS[i]
        result = _fd_relay_step(relay[2], relay[3], relay[4], relay[5], relay[6])
        if _is_none(result):
            _mark_task_done(relay[0])
            _mark_task_done(relay[1])
            _ACTIVE_RELAYS.pop(i)
            progressed = True
            continue
        if not _is_none(_fd_relay_step_last_progress()):
            progressed = True
        relay[6] = result
        i += 1
    return progressed


class Protocol:
    def connection_made(self, transport):
        self.transport = transport

    def connection_lost(self, exc):
        return None

    def data_received(self, data):
        return None

    def eof_received(self):
        return None

    def get_buffer(self, sizehint):
        return bytearray(sizehint)

    def buffer_updated(self, nbytes):
        return None


class DatagramProtocol(Protocol):
    def datagram_received(self, data, addr):
        return None

    def error_received(self, exc):
        return None


class _AppTransport(Transport):
    def write(self, data):
        return None


class _SSLProtocol(Protocol):
    def __init__(
        self,
        loop=None,
        app_protocol=None,
        sslcontext=None,
        waiter=None,
        server_side=False,
        server_hostname=None,
        call_connection_made=True,
    ) -> None:
        self._loop = loop
        self._app_protocol = app_protocol
        self._sslcontext = sslcontext
        self._waiter = waiter
        self._server_side = server_side
        self._server_hostname = server_hostname
        self._app_transport = _AppTransport()
        self._closed = False
        if call_connection_made and app_protocol is not None:
            app_protocol.connection_made(self._app_transport)

    def connection_made(self, transport):
        self._transport = transport
        if self._app_protocol is not None:
            self._app_protocol.connection_made(self._app_transport)

    def connection_lost(self, exc):
        self._closed = True
        self._app_transport._closed = True
        if self._app_protocol is not None:
            self._app_protocol.connection_lost(exc)

    def data_received(self, data):
        if self._app_protocol is not None:
            self._app_protocol.data_received(data)

    def eof_received(self):
        self._closed = True
        self._app_transport._closed = True
        if self._app_protocol is not None:
            return self._app_protocol.eof_received()
        return None

    def get_buffer(self, sizehint):
        return bytearray(sizehint)

    def buffer_updated(self, nbytes):
        return None


class _SSLProtoModule:
    SSLProtocol = _SSLProtocol


sslproto = _SSLProtoModule()


class _Loop:
    def __init__(self) -> None:
        self._closed = False

    def create_future(self):
        return Future()

    def create_task(self, coro):
        return ensure_future(coro)

    def run_until_complete(self, awaitable):
        return _py_await(awaitable)

    def run_forever(self):
        while True:
            active = False
            progressed = False
            for server in _SERVERS:
                if not server._closed:
                    active = True
                    if server._accept_once():
                        progressed = True
            if _drive_relays():
                progressed = True
            if len(_ACTIVE_RELAYS) > 0:
                # keep the loop alive to finish relays even if servers closed
                active = True
            if not active:
                break
            # Step pending tasks and DROP finished ones, so _TASKS cannot grow
            # without bound (every accepted connection schedules tasks; leaving
            # the completed ones in the list turned each loop pass into an O(N)
            # walk over all historical tasks -> rising CPU, rising RSS, and an
            # eventual livelock under sustained load). progressed is only set
            # when a not-yet-done task actually runs, so an idle loop can sleep
            # instead of spinning at 100% CPU.
            i = 0
            while i < len(_TASKS):
                task = _TASKS[i]
                if task.done():
                    _TASKS.pop(i)
                    continue
                task._step()
                progressed = True
                if task.done():
                    _TASKS.pop(i)
                else:
                    i += 1
            if not progressed:
                _usleep(1000)

    def stop(self):
        return None

    def close(self):
        self._closed = True

    def is_closed(self):
        return self._closed

    def shutdown_asyncgens(self):
        return _completed_future(None)

    def call_soon(self, callback, *args, **kwargs):
        return callback(*args)

    def call_later(self, delay, callback, *args, **kwargs):
        return callback(*args)

    def time(self):
        return 0.0

    def add_reader(self, fd, callback, *args):
        return None

    def remove_reader(self, fd):
        return False

    def run_in_executor(self, executor, fn, *args):
        return fn(*args)

    def getaddrinfo(self, *args, **kwargs):
        raise NotImplementedError("asyncio.getaddrinfo awaits native event-loop I/O")

    def create_datagram_endpoint(self, *args, **kwargs):
        raise NotImplementedError(
            "asyncio.create_datagram_endpoint awaits native event-loop I/O"
        )

    def create_connection(self, *args, **kwargs):
        raise NotImplementedError("asyncio.create_connection awaits native event-loop I/O")


class _Server:
    def __init__(self, loop, fd, handler) -> None:
        self._loop = loop
        self._fd = fd
        self._handler = handler
        self._closed = False
        self.sockets = [_Socket(fd)]

    def close(self):
        if not self._closed:
            self._closed = True
            for sock in self.sockets:
                sock.close()

    def wait_closed(self):
        return _completed_future(None)

    def _accept_once(self):
        client_fd = _tcp_accept(self._fd)
        if _is_none(client_fd):
            return False
        sock = _Socket(client_fd)
        transport = _SocketTransport(client_fd, sock)
        reader = StreamReader()
        reader.set_transport(transport)
        writer = StreamWriter(transport, None, reader, self._loop)
        result = self._handler(reader, writer)
        if not _is_none(result):
            _py_await(result)
        return True


def get_event_loop():
    loop = _LOOP_BOX[0]
    if _is_none(loop):
        loop = _Loop()
        _LOOP_BOX[0] = loop
    return loop


def new_event_loop():
    return _Loop()


def io_waitset_backend():
    """Name of the IO-waitset readiness backend this platform provides.

    Returns "kqueue" on Darwin/BSD (the scalable kevent(2) notifier in
    py_io_waitset.c) and "poll" elsewhere (the level-triggered poll fallback).
    Lets the event loop pick the notifier over the O(n) poll rescan.
    """
    return _io_waitset_backend()


def set_event_loop(loop):
    _LOOP_BOX[0] = loop


def run(awaitable, debug=None):
    return _py_await(awaitable)


def sleep(delay, result=None):
    return _py_asyncio_sleep(delay)


def ensure_future(awaitable, loop=None):
    if isinstance(awaitable, Task):
        return awaitable
    task = Task(awaitable)
    _TASKS.append(task)
    _try_stream_relay(awaitable, task)
    return task


def create_task(awaitable):
    return ensure_future(awaitable)


def all_tasks(loop=None):
    return list(_TASKS)


def current_task(loop=None):
    if _TASKS:
        return _TASKS[0]
    return None


def wait_for(awaitable, timeout=None):
    return awaitable


async def wait(fs, timeout=None, return_when=None):
    return set(fs), set()


async def gather(*aws, return_exceptions=False):
    out = []
    for aw in aws:
        out.append(await aw)
    return out


def open_connection(*args, **kwargs):
    host = None
    port = None
    if len(args) > 0:
        host = args[0]
    if len(args) > 1:
        port = args[1]
    if "host" in kwargs:
        host = kwargs.get("host")
    if "port" in kwargs:
        port = kwargs.get("port")
    fd = _tcp_connect(host, port)
    if _is_none(fd):
        raise OSError("asyncio.open_connection failed")
    sock = _Socket(fd)
    transport = _SocketTransport(fd, sock)
    reader = StreamReader()
    reader.set_transport(transport)
    writer = StreamWriter(transport, None, reader, get_event_loop())
    return _completed_future((reader, writer))


async def open_unix_connection(*args, **kwargs):
    raise NotImplementedError(
        "asyncio.open_unix_connection awaits native event-loop I/O"
    )


def start_server(*args, **kwargs):
    if len(args) == 0:
        client_connected_cb = kwargs.get("client_connected_cb")
    else:
        client_connected_cb = args[0]
    host = kwargs.get("host")
    port = kwargs.get("port")
    if len(args) > 1:
        host = args[1]
    if len(args) > 2:
        port = args[2]
    reuse_port = 1 if kwargs.get("reuse_port") else 0
    fd = _tcp_listen(host, port, reuse_port)
    if _is_none(fd):
        raise OSError("asyncio.start_server failed")
    loop = get_event_loop()
    server = _Server(loop, fd, client_connected_cb)
    _SERVERS.append(server)
    return _completed_future(server)


async def start_unix_server(*args, **kwargs):
    raise NotImplementedError("asyncio.start_unix_server awaits native event-loop I/O")
