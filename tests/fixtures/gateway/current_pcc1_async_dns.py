"""Current-pcc1 deterministic async-DNS driver acceptance source.

This proves the pcc-owned nonblocking state machine, cache and policy shape in
a self/no-libpython artifact.  Live UDP/waitset execution is a separate
hardware/network gate even though the production adapter source now exists.
"""

from pcc.gateway.dns import (
    DNS_A,
    DNS_IO_OK,
    DNS_IO_WOULD_BLOCK,
    DnsIoResult,
    DnsResolverConfig,
    DnsServer,
    Resolver,
)
from pcc.gateway.dns_native import NativeDnsTransport


def u16(value: int) -> bytes:
    return bytes(((value >> 8) & 255, value & 255))


def response(query: bytes) -> bytes:
    output = bytearray(query[:2])
    output.extend(b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00")
    output.extend(query[12:])
    output.extend(b"\xc0\x0c\x00\x01\x00\x01")
    output.extend(b"\x00\x00\x00\x1e\x00\x04\xc0\x00\x02\x07")
    return bytes(output)


class DeterministicTransport:
    def __init__(self) -> None:
        self.reply = b""
        self.reads = 0
        self.closed = 0

    def open(self, protocol, server):
        return DnsIoResult(DNS_IO_OK, handle=11)

    def connect(self, handle, server):
        return DnsIoResult(DNS_IO_OK, handle=handle)

    def send(self, handle, data, offset):
        return DnsIoResult(DNS_IO_OK, handle=handle, count=len(data) - offset)

    def receive(self, handle, max_bytes):
        self.reads += 1
        if self.reads == 1:
            return DnsIoResult(DNS_IO_WOULD_BLOCK, handle=handle)
        return DnsIoResult(
            DNS_IO_OK,
            handle=handle,
            data=self.reply,
            peer=DnsServer("192.0.2.53"),
        )

    def close(self, handle) -> None:
        self.closed += 1


def main() -> int:
    native = NativeDnsTransport()
    if native.owned:
        return 9
    transport = DeterministicTransport()
    resolver = Resolver(
        config=DnsResolverConfig((DnsServer("192.0.2.53"),), 1, 500)
    )
    driver = resolver.begin_driver("example.com", DNS_A, 100, 1000, transport)
    transport.reply = response(driver.operation.query)
    result = driver.step(100)
    while result.kind == "progress":
        result = driver.step(100)
    if result.kind != "wait-read" or result.deadline_ms != 600:
        return 1
    result = driver.step(101)
    while result.kind == "progress":
        result = driver.step(101)
    if result.kind != "complete" or result.values != ["192.0.2.7"]:
        return 2
    if transport.closed != 1:
        return 3
    cached = resolver.begin_driver("example.com", DNS_A, 102, 1000, transport).step(102)
    if cached.kind != "complete" or cached.source != "cache":
        return 4
    print("PCC1_GATEWAY_ASYNC_DNS_MODEL_OK")
    return 0


main()
