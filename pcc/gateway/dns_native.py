"""pcc-owned live DNS transport for :mod:`pcc.gateway.dns`.

The adapter uses only compiler-known pcc runtime ABIs.  It never imports the
host ``socket`` module, calls ``getaddrinfo`` or enters libc's resolver.  UDP
descriptors are connected to one configured numeric nameserver before use, so
the kernel supplies peer provenance and drops datagrams from other peers.  A
truncated reply is reopened as nonblocking TCP by ``DnsResolveDriver`` through
the same adapter.
"""

from pcc.extern import c_int64, c_ptr, extern
from pcc.py_runtime.py.py_abi_constants import PYBYTESOBJECT_DATA_OFFSET
from pcc.unsafe import load_i64, ptr_add, stack_alloc

from .dns import (
    DNS_IO_EOF,
    DNS_IO_ERROR,
    DNS_IO_OK,
    DNS_IO_WOULD_BLOCK,
    DnsError,
    DnsIoResult,
    HostsTable,
    Resolver,
    parse_resolver_config,
)


PCC_SOCKET_PROGRESS = 0
PCC_SOCKET_WOULD_BLOCK = 1
PCC_SOCKET_EOF = 2
PCC_SOCKET_CONNECTED = 3

_DNS_PROTOCOL_UDP = 0
_DNS_PROTOCOL_TCP = 1
_SYSTEM_TEXT_LIMIT = 65535


_platform_dns_connect_start = extern(
    "pcc_platform_dns_connect_start",
    (c_int64, c_ptr, c_ptr, c_ptr),
    c_int64,
)
_platform_udp_connect_start = extern(
    "pcc_platform_udp_connect_start", (c_ptr, c_ptr, c_ptr), c_int64
)
_platform_socket_connect_observe = extern(
    "pcc_platform_socket_connect_observe", (c_int64, c_int64), c_int64
)
_platform_socket_read_observe = extern(
    "pcc_platform_socket_read_observe",
    (c_int64, c_ptr, c_int64, c_int64, c_ptr),
    c_int64,
)
_platform_socket_write_observe = extern(
    "pcc_platform_socket_write_observe",
    (c_int64, c_ptr, c_int64, c_int64, c_ptr),
    c_int64,
)
_platform_resolver_config_read = extern(
    "pcc_platform_resolver_config_read", (c_ptr, c_int64), c_int64
)
_platform_hosts_config_read = extern(
    "pcc_platform_hosts_config_read", (c_ptr, c_int64), c_int64
)
_platform_random_u16 = extern("pcc_platform_random_u16", (), c_int64)
_platform_close = extern("pcc_platform_close", (c_int64,), c_int64)
_py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
_py_bytes_new = extern("py_bytes_new", (c_ptr, c_int64), c_ptr)


def _map_socket_outcome(outcome: int) -> int:
    if outcome == PCC_SOCKET_PROGRESS or outcome == PCC_SOCKET_CONNECTED:
        return DNS_IO_OK
    if outcome == PCC_SOCKET_WOULD_BLOCK:
        return DNS_IO_WOULD_BLOCK
    if outcome == PCC_SOCKET_EOF:
        return DNS_IO_EOF
    return DNS_IO_ERROR


class NativeDnsTransport:
    """Immediate nonblocking observations over pcc platform descriptors."""

    native_virtual_threads = True

    def __init__(self) -> None:
        # handle -> (configured server, protocol, connect outcome).  Keeping
        # this ownership record is what lets receive() attach verified peer
        # provenance without parsing an untrusted source sockaddr in Python.
        self.owned = {}

    def fork(self):
        """Return a connection-owned descriptor provenance table."""
        return NativeDnsTransport()

    def open(self, protocol: str, server) -> DnsIoResult:
        if protocol == "udp":
            protocol_code = _DNS_PROTOCOL_UDP
        elif protocol == "tcp":
            protocol_code = _DNS_PROTOCOL_TCP
        else:
            return DnsIoResult(DNS_IO_ERROR, error="unsupported-dns-protocol")
        output_fd = stack_alloc(8)
        if protocol_code == _DNS_PROTOCOL_UDP:
            outcome = _platform_udp_connect_start(
                _py_str_utf8(server.address),
                _py_str_utf8(str(server.port)),
                output_fd,
            )
        else:
            outcome = _platform_dns_connect_start(
                protocol_code,
                _py_str_utf8(server.address),
                _py_str_utf8(str(server.port)),
                output_fd,
            )
        handle = load_i64(output_fd, 0)
        if handle < 0 or outcome not in (
            PCC_SOCKET_CONNECTED,
            PCC_SOCKET_WOULD_BLOCK,
        ):
            if handle >= 0:
                _platform_close(handle)
            return DnsIoResult(
                DNS_IO_ERROR,
                error="dns-socket-open:" + str(outcome),
            )
        connect_state = outcome
        self.owned[handle] = (server, protocol, connect_state)
        return DnsIoResult(DNS_IO_OK, handle=handle)

    def connect(self, handle, server) -> DnsIoResult:
        owner = self.owned.get(handle)
        if owner is None or owner[0] is not server:
            return DnsIoResult(
                DNS_IO_ERROR,
                handle=handle,
                error="dns-handle-provenance",
            )
        if owner[2] == PCC_SOCKET_CONNECTED:
            return DnsIoResult(DNS_IO_OK, handle=handle)
        outcome = _platform_socket_connect_observe(handle, 0)
        status = _map_socket_outcome(outcome)
        if status == DNS_IO_OK:
            self.owned[handle] = (owner[0], owner[1], PCC_SOCKET_CONNECTED)
        return DnsIoResult(
            status,
            handle=handle,
            error="" if status != DNS_IO_ERROR else "dns-connect:" + str(outcome),
        )

    def send(self, handle, data: bytes, offset: int) -> DnsIoResult:
        owner = self.owned.get(handle)
        if owner is None or owner[2] != PCC_SOCKET_CONNECTED:
            return DnsIoResult(
                DNS_IO_ERROR, handle=handle, error="dns-handle-provenance"
            )
        if offset < 0 or offset > len(data):
            return DnsIoResult(
                DNS_IO_ERROR, handle=handle, error="invalid-send-offset"
            )
        output_count = stack_alloc(8)
        outcome = _platform_socket_write_observe(
            handle,
            ptr_add(data, PYBYTESOBJECT_DATA_OFFSET + offset),
            len(data) - offset,
            0,
            output_count,
        )
        status = _map_socket_outcome(outcome)
        return DnsIoResult(
            status,
            handle=handle,
            count=load_i64(output_count, 0),
            error="" if status != DNS_IO_ERROR else "dns-send:" + str(outcome),
        )

    def receive(self, handle, max_bytes: int) -> DnsIoResult:
        owner = self.owned.get(handle)
        if owner is None or owner[2] != PCC_SOCKET_CONNECTED:
            return DnsIoResult(
                DNS_IO_ERROR, handle=handle, error="dns-handle-provenance"
            )
        if max_bytes <= 0:
            return DnsIoResult(
                DNS_IO_ERROR, handle=handle, error="invalid-receive-limit"
            )
        if max_bytes > _SYSTEM_TEXT_LIMIT:
            max_bytes = _SYSTEM_TEXT_LIMIT
        # UDP must observe one whole datagram.  Reading only max_udp_bytes can
        # silently truncate an oversized packet at the syscall boundary and
        # make it look like a merely malformed in-policy reply.  Reserve one
        # extra byte so the driver can reject the datagram explicitly.
        receive_capacity = max_bytes
        if owner[1] == "udp" and receive_capacity < _SYSTEM_TEXT_LIMIT:
            receive_capacity = receive_capacity + 1
        storage = stack_alloc(_SYSTEM_TEXT_LIMIT)
        output_count = stack_alloc(8)
        outcome = _platform_socket_read_observe(
            handle, storage, receive_capacity, 0, output_count
        )
        status = _map_socket_outcome(outcome)
        count = load_i64(output_count, 0)
        data = b""
        if status == DNS_IO_OK and count > 0:
            data = _py_bytes_new(storage, count)
        return DnsIoResult(
            status,
            handle=handle,
            count=count,
            data=data,
            # The socket was connected to this exact numeric server by
            # pcc_platform_dns_connect_start.  This marker is not derived from
            # packet data and therefore cannot be forged by a remote sender.
            peer=owner[0],
            error="" if status != DNS_IO_ERROR else "dns-receive:" + str(outcome),
        )

    def close(self, handle) -> None:
        if handle not in self.owned:
            return
        del self.owned[handle]
        _platform_close(handle)

    def _read_platform_text(self, kind: int, label: str) -> str:
        storage = stack_alloc(_SYSTEM_TEXT_LIMIT)
        if kind == 0:
            count = _platform_resolver_config_read(
                storage, _SYSTEM_TEXT_LIMIT
            )
        else:
            count = _platform_hosts_config_read(storage, _SYSTEM_TEXT_LIMIT)
        if count < 0:
            raise DnsError(label + "-read", "cannot read system " + label)
        if count == 0:
            return ""
        if count >= _SYSTEM_TEXT_LIMIT:
            raise DnsError(
                label + "-too-large",
                "system " + label + " exceeds gateway limit",
            )
        return _py_bytes_new(storage, count).decode("utf-8", "ignore")

    def create_system_resolver(self) -> Resolver:
        resolver_text = self._read_platform_text(
            0, "resolver-config"
        )
        hosts_text = self._read_platform_text(
            1, "hosts-config"
        )
        config = parse_resolver_config(resolver_text)
        seed = _platform_random_u16()
        if seed <= 0:
            raise DnsError(
                "dns-entropy-unavailable",
                "cannot seed DNS transaction identifiers",
            )
        return Resolver(
            config=config,
            hosts=HostsTable(hosts_text),
            query_seed=seed,
        )


class LazySystemResolver:
    """Publish immutable resolver inputs and fork connection-owned state.

    With no explicit ``resolver`` the snapshot is loaded from the bounded
    platform resolver/hosts files during server startup.  Supplying a Resolver
    keeps the same live native transport while allowing an application to
    publish an explicit numeric nameserver set (for containers, split service
    networks and the product canary) without entering a libc resolver.
    """

    def __init__(self, transport: NativeDnsTransport, resolver=None) -> None:
        if resolver is not None and not isinstance(resolver, Resolver):
            raise TypeError("native DNS resolver snapshot must be Resolver")
        self.transport = transport
        self.resolver = resolver

    def _get(self) -> Resolver:
        if self.resolver is None:
            self.resolver = self.transport.create_system_resolver()
        return self.resolver

    def prepare(self) -> None:
        self._get()

    def fork(self) -> Resolver:
        base = self._get()
        seed = _platform_random_u16()
        if seed <= 0:
            raise DnsError(
                "dns-entropy-unavailable",
                "cannot seed DNS transaction identifiers",
            )
        # Config/hosts are immutable after publication.  Cache, rebinding
        # history and query-id state remain connection-owned, avoiding data
        # races across multiple pcc carrier threads.
        return Resolver(
            config=base.config,
            hosts=base.hosts,
            query_seed=seed,
        )

    def begin_driver(
        self,
        name: str,
        qtype: int,
        now_ms: int,
        deadline_ms: int,
        transport=None,
    ):
        return self._get().begin_driver(
            name,
            qtype,
            now_ms,
            deadline_ms,
            self.transport if transport is None else transport,
        )


__all__ = ["NativeDnsTransport", "LazySystemResolver"]
