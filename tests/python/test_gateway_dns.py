"""DNS wire, live-adapter contract, policy and current-pcc1 source gates."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import pcc.gateway.dns_native as dns_native

from pcc.gateway.dns import (
    DNS_A,
    DNS_AAAA,
    DNS_INTEREST_READ,
    DNS_IO_EOF,
    DNS_IO_ERROR,
    DNS_IO_OK,
    DNS_IO_WOULD_BLOCK,
    DnsAddressPolicy,
    DnsCache,
    DnsError,
    DnsIoResult,
    DnsResolverConfig,
    DnsServer,
    HostsTable,
    Resolver,
    build_query,
    encode_name,
    normalize_numeric_address,
    parse_response,
    parse_resolver_config,
)
from pcc.gateway.dns_native import LazySystemResolver, NativeDnsTransport
from pcc1_gate import find_current_pcc1


REPO = Path(__file__).resolve().parents[2]
PCC1_DNS_SOURCE = (
    REPO / "tests" / "fixtures" / "gateway" / "current_pcc1_async_dns.py"
)


def _u16_bytes(value: int) -> bytes:
    return bytes(((value >> 8) & 255, value & 255))


def _u32_bytes(value: int) -> bytes:
    return bytes(
        (
            (value >> 24) & 255,
            (value >> 16) & 255,
            (value >> 8) & 255,
            value & 255,
        )
    )


def _a_response(
    query: bytes,
    address=(192, 0, 2, 7),
    ttl: int = 30,
    truncated: bool = False,
    rcode: int = 0,
    owner: bytes = b"\xc0\x0c",
) -> bytes:
    flags = 0x8180 | rcode
    if truncated:
        flags |= 0x0200
    answer_count = 0 if rcode else 1
    output = bytearray(query[:2])
    output.extend(_u16_bytes(flags))
    output.extend(b"\x00\x01")
    output.extend(_u16_bytes(answer_count))
    output.extend(b"\x00\x00\x00\x00")
    output.extend(query[12:])
    if answer_count:
        output.extend(owner)
        output.extend(b"\x00\x01\x00\x01")
        output.extend(_u32_bytes(ttl))
        output.extend(b"\x00\x04")
        output.extend(bytes(address))
    return bytes(output)


def _aaaa_response(query: bytes, address: bytes, ttl: int = 30) -> bytes:
    output = bytearray(query[:2])
    output.extend(b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00")
    output.extend(query[12:])
    output.extend(b"\xc0\x0c\x00\x1c\x00\x01")
    output.extend(_u32_bytes(ttl))
    output.extend(b"\x00\x10")
    output.extend(address)
    return bytes(output)


class ScriptedTransport:
    """Immediate observation adapter; it never opens a host socket."""

    def __init__(self) -> None:
        self.next_handle = 40
        self.opens = []
        self.connect_results = []
        self.send_results = []
        self.receive_results = []
        self.sent = []
        self.closed = []
        self.owners = {}
        self.attach_owner = True

    def open(self, protocol, server):
        handle = self.next_handle
        self.next_handle += 1
        self.opens.append((protocol, server.address, server.port, handle))
        self.owners[handle] = server
        return DnsIoResult(DNS_IO_OK, handle=handle)

    def connect(self, handle, server):
        if self.connect_results:
            result = self.connect_results.pop(0)
            result.handle = handle
            return result
        return DnsIoResult(DNS_IO_OK, handle=handle)

    def send(self, handle, data, offset):
        self.sent.append((handle, bytes(data[offset:])))
        if self.send_results:
            result = self.send_results.pop(0)
            result.handle = handle
            return result
        return DnsIoResult(DNS_IO_OK, handle=handle, count=len(data) - offset)

    def receive(self, handle, max_bytes):
        if not self.receive_results:
            return DnsIoResult(DNS_IO_WOULD_BLOCK, handle=handle)
        result = self.receive_results.pop(0)
        result.handle = handle
        if result.peer is None and self.attach_owner:
            result.peer = self.owners[handle]
        return result

    def close(self, handle):
        self.closed.append(handle)
        if handle in self.owners:
            del self.owners[handle]


def _advance(driver, now_ms: int, terminal=("complete", "error", "wait-read", "wait-write")):
    steps = 0
    while steps < 64:
        result = driver.step(now_ms)
        if result.kind in terminal:
            return result
        steps += 1
    raise AssertionError("DNS driver made no terminal/readiness progress")


def _resolver(
    transport: ScriptedTransport,
    *,
    servers=("192.0.2.53",),
    attempts_per_server=1,
    attempt_timeout_ms=500,
    cache=None,
    hosts=None,
    policy=None,
):
    config = DnsResolverConfig(
        tuple(DnsServer(server) for server in servers),
        attempts_per_server=attempts_per_server,
        attempt_timeout_ms=attempt_timeout_ms,
    )
    return Resolver(cache=cache, config=config, hosts=hosts, policy=policy)


def test_query_and_response_are_id_and_question_bound() -> None:
    query = build_query(7, "example.com", DNS_A)
    response = parse_response(_a_response(query), 7, "example.com", DNS_A)
    assert response.answers[0].data == "192.0.2.7"
    with pytest.raises(DnsError, match="query") as caught:
        parse_response(_a_response(query), 8, "example.com", DNS_A)
    assert caught.value.code == "id-mismatch"


def test_resolver_query_seed_is_nonzero_u16_and_advances() -> None:
    resolver = Resolver(query_seed=65535)
    first = resolver.begin("one.example", DNS_A, 0, 1000)
    second = resolver.begin("two.example", DNS_A, 0, 1000)
    assert first.query_id == 65535
    assert second.query_id == 1
    with pytest.raises(ValueError, match="query seed"):
        Resolver(query_seed=0)


def test_numeric_and_preloaded_hosts_fast_paths_do_no_transport_io() -> None:
    transport = ScriptedTransport()
    hosts = HostsTable("127.0.0.9 api.internal\n2001:db8::9 api-v6.internal\n")
    resolver = _resolver(transport, hosts=hosts)

    numeric = resolver.begin_driver("192.0.2.8", DNS_A, 0, 1000, transport)
    numeric_result = numeric.step(0)
    assert numeric_result.kind == "complete"
    assert numeric_result.source == "numeric"
    assert numeric_result.values == ["192.0.2.8"]

    local = resolver.begin_driver("api.internal", DNS_A, 0, 1000, transport)
    local_result = local.step(0)
    assert local_result.kind == "complete"
    assert local_result.source == "hosts"
    assert local_result.values == ["127.0.0.9"]
    assert transport.opens == []


def test_numeric_parser_is_strict_and_canonical_for_a_and_aaaa() -> None:
    assert normalize_numeric_address("192.0.2.1", DNS_A) == "192.0.2.1"
    assert normalize_numeric_address("192.000.2.1", DNS_A) is None
    assert normalize_numeric_address("2001:DB8::1", DNS_AAAA) == (
        "2001:db8:0:0:0:0:0:1"
    )
    assert normalize_numeric_address("fe80::1%en0", DNS_AAAA) is None


def test_system_resolver_config_is_bounded_numeric_and_policy_owned() -> None:
    config = parse_resolver_config(
        """
        nameserver resolver.example
        nameserver 192.0.2.53
        nameserver 2001:db8::53 # retained without a host lookup
        nameserver 192.0.2.53
        options timeout:3 attempts:4 rotate use-vc ndots:5
        search ignored.example
        """
    )
    assert [server.address for server in config.servers] == [
        "192.0.2.53",
        "2001:db8:0:0:0:0:0:53",
    ]
    assert config.attempt_timeout_ms == 3000
    assert config.attempts_per_server == 4
    assert config.rotate
    assert config.use_tcp

    clamped = parse_resolver_config(
        "nameserver 192.0.2.53\noptions timeout:999 attempts:0\n"
    )
    assert clamped.attempt_timeout_ms == 30000
    assert clamped.attempts_per_server == 1


def test_live_dns_adapter_source_uses_owned_connected_socket_abi_only() -> None:
    source = (REPO / "pcc" / "gateway" / "dns_native.py").read_text(
        encoding="utf-8"
    )
    runtime = (
        REPO
        / "pcc"
        / "py_runtime"
        / "py"
        / "freestanding_platform_socket.py"
    ).read_text(encoding="utf-8")
    server = (REPO / "pcc" / "gateway" / "server.py").read_text(
        encoding="utf-8"
    )
    assert '"pcc_platform_dns_connect_start"' in source
    assert '"pcc_platform_udp_connect_start"' in source
    assert 'peer=owner[0]' in source
    assert 'cstr("/etc/resolv.conf")' in runtime
    assert 'cstr("/etc/hosts")' in runtime
    assert '"pcc_platform_random_u16"' in source
    assert "connection_resolver = connection_resolver.fork()" in server
    assert "connection_dns_transport.fork()" in server
    # The module docstring names the forbidden host resolver while explaining
    # the ownership boundary; reject executable call/symbol shapes rather than
    # that documentation text.
    assert "getaddrinfo(" not in source
    assert '"getaddrinfo"' not in source
    assert "import socket" not in source
    assert NativeDnsTransport.native_virtual_threads


def test_live_dns_adapter_fork_owns_an_independent_provenance_table() -> None:
    transport = NativeDnsTransport()
    forked = transport.fork()
    assert forked is not transport
    assert forked.owned is not transport.owned


def test_explicit_native_resolver_snapshot_avoids_host_resolver_configuration(
    monkeypatch,
) -> None:
    transport = NativeDnsTransport()
    base = Resolver(
        config=DnsResolverConfig((DnsServer("127.0.0.1", 5353),)),
        hosts=HostsTable(),
        query_seed=17,
    )
    published = LazySystemResolver(transport, base)
    monkeypatch.setattr(dns_native, "_platform_random_u16", lambda: 19)
    published.prepare()
    forked = published.fork()
    assert published.resolver is base
    assert forked is not base
    assert forked.config is base.config
    assert forked.hosts is base.hosts
    assert forked.cache is not base.cache


def test_driver_returns_exact_read_wait_and_absolute_attempt_deadline() -> None:
    transport = ScriptedTransport()
    resolver = _resolver(transport, attempt_timeout_ms=250)
    driver = resolver.begin_driver("example.com", DNS_A, 100, 1000, transport)
    transport.receive_results.append(DnsIoResult(DNS_IO_WOULD_BLOCK))

    waiting = _advance(driver, 100)
    assert waiting.kind == "wait-read"
    assert waiting.interest == DNS_INTEREST_READ
    assert waiting.deadline_ms == 350
    assert waiting.handle == 40

    transport.receive_results.append(
        DnsIoResult(DNS_IO_OK, data=_a_response(driver.operation.query))
    )
    completed = _advance(driver, 101)
    assert completed.kind == "complete"
    assert completed.values == ["192.0.2.7"]
    assert transport.closed == [40]


def test_udp_truncation_reopens_tcp_and_retains_partial_frame_progress() -> None:
    transport = ScriptedTransport()
    resolver = _resolver(transport)
    driver = resolver.begin_driver("example.com", DNS_A, 0, 2000, transport)
    truncated = _a_response(driver.operation.query, truncated=True)
    full = _a_response(driver.operation.query, address=(198, 51, 100, 4))
    framed = _u16_bytes(len(full)) + full
    transport.receive_results.extend(
        (
            DnsIoResult(DNS_IO_OK, data=truncated, peer="192.0.2.53"),
            DnsIoResult(DNS_IO_OK, data=framed[:1]),
            DnsIoResult(DNS_IO_OK, data=framed[1:9]),
            DnsIoResult(DNS_IO_OK, data=framed[9:]),
        )
    )

    completed = _advance(driver, 10, terminal=("complete", "error"))
    assert completed.kind == "complete"
    assert completed.values == ["198.51.100.4"]
    assert [opened[0] for opened in transport.opens] == ["udp", "tcp"]
    assert transport.sent[1][1][:2] == _u16_bytes(len(driver.operation.query))
    assert transport.closed == [40, 41]


def test_transport_failure_rotates_servers_and_preserves_global_deadline() -> None:
    transport = ScriptedTransport()
    resolver = _resolver(
        transport,
        servers=("192.0.2.53", "198.51.100.53"),
        attempts_per_server=1,
        attempt_timeout_ms=900,
    )
    driver = resolver.begin_driver("example.com", DNS_A, 100, 1000, transport)
    transport.receive_results.extend(
        (
            DnsIoResult(DNS_IO_ERROR, error="network-unreachable"),
            DnsIoResult(DNS_IO_WOULD_BLOCK),
        )
    )

    waiting = _advance(driver, 200)
    assert waiting.kind == "wait-read"
    assert waiting.server.address == "198.51.100.53"
    assert waiting.deadline_ms == 1000
    assert [opened[1] for opened in transport.opens] == [
        "192.0.2.53",
        "198.51.100.53",
    ]
    assert transport.closed == [40]


def test_attempts_per_server_are_exhausted_before_server_rotation() -> None:
    transport = ScriptedTransport()
    resolver = _resolver(
        transport,
        servers=("192.0.2.53", "198.51.100.53"),
        attempts_per_server=2,
    )
    driver = resolver.begin_driver("example.com", DNS_A, 0, 2000, transport)
    transport.receive_results.extend(
        (
            DnsIoResult(DNS_IO_ERROR, error="first"),
            DnsIoResult(DNS_IO_ERROR, error="second"),
            DnsIoResult(DNS_IO_WOULD_BLOCK),
        )
    )
    waiting = _advance(driver, 10)
    assert waiting.kind == "wait-read"
    assert [opened[1] for opened in transport.opens] == [
        "192.0.2.53",
        "192.0.2.53",
        "198.51.100.53",
    ]


def test_resolver_use_vc_starts_with_framed_tcp_query() -> None:
    transport = ScriptedTransport()
    resolver = Resolver(
        config=DnsResolverConfig(
            (DnsServer("192.0.2.53"),),
            attempts_per_server=1,
            use_tcp=True,
        )
    )
    driver = resolver.begin_driver("example.com", DNS_A, 0, 1000, transport)
    transport.receive_results.append(DnsIoResult(DNS_IO_WOULD_BLOCK))

    waiting = _advance(driver, 1)
    assert waiting.kind == "wait-read"
    assert transport.opens[0][0] == "tcp"
    assert transport.sent[0][1][:2] == _u16_bytes(len(driver.operation.query))


def test_mismatched_udp_peer_and_query_id_are_bounded_ignored_replies() -> None:
    transport = ScriptedTransport()
    resolver = _resolver(transport)
    driver = resolver.begin_driver("example.com", DNS_A, 0, 1000, transport)
    wrong_id = bytearray(_a_response(driver.operation.query))
    wrong_id[1] ^= 1
    transport.receive_results.extend(
        (
            DnsIoResult(DNS_IO_OK, data=_a_response(driver.operation.query), peer="203.0.113.53"),
            DnsIoResult(DNS_IO_OK, data=bytes(wrong_id), peer="192.0.2.53"),
            DnsIoResult(DNS_IO_OK, data=_a_response(driver.operation.query), peer="192.0.2.53"),
        )
    )

    first = _advance(driver, 10, terminal=("ignored", "error"))
    assert first.kind == "ignored" and first.error == "server-mismatch"
    second = _advance(driver, 11, terminal=("ignored", "error"))
    assert second.kind == "ignored" and second.error == "id-mismatch"
    completed = _advance(driver, 12, terminal=("complete", "error"))
    assert completed.kind == "complete"


def test_udp_reply_without_connected_peer_provenance_is_rejected() -> None:
    transport = ScriptedTransport()
    transport.attach_owner = False
    resolver = _resolver(transport)
    driver = resolver.begin_driver("example.com", DNS_A, 0, 1000, transport)
    transport.receive_results.append(
        DnsIoResult(
            DNS_IO_OK,
            data=_a_response(driver.operation.query),
            peer="",
        )
    )
    result = _advance(driver, 1, terminal=("ignored", "error"))
    assert result.kind == "ignored"
    assert result.error == "server-mismatch"


def test_nxdomain_is_negative_cached_and_expires_at_configured_ttl() -> None:
    cache = DnsCache(negative_ttl_ms=40, min_ttl_ms=0)
    transport = ScriptedTransport()
    resolver = _resolver(transport, cache=cache)
    driver = resolver.begin_driver("missing.example", DNS_A, 0, 1000, transport)
    transport.receive_results.append(
        DnsIoResult(DNS_IO_OK, data=_a_response(driver.operation.query, rcode=3))
    )
    result = _advance(driver, 1, terminal=("complete", "error"))
    assert result.kind == "error" and result.error == "nxdomain"

    cached = resolver.begin_driver("missing.example", DNS_A, 20, 1000, transport)
    cached_result = cached.step(20)
    assert cached_result.kind == "error"
    assert cached_result.error == "cached-negative"
    expired = resolver.begin_driver("missing.example", DNS_A, 41, 1000, transport)
    assert expired.operation is not None


def test_ttl_cache_is_clamped_and_expired_without_host_lookup() -> None:
    cache = DnsCache(min_ttl_ms=100, max_ttl_ms=200)
    resolver = Resolver(cache=cache)
    operation = resolver.begin("example.com", DNS_A, 1000, 2000)
    operation.receive(_a_response(operation.query, ttl=10), 1100)
    assert resolver.begin("example.com", DNS_A, 1250, 2000).values == ["192.0.2.7"]
    assert cache.get("example.com", DNS_A, 1300) is None


def test_policy_rejects_private_result_and_detects_public_to_private_rebind() -> None:
    strict = DnsAddressPolicy(allow_private=False, allow_loopback=False)
    with pytest.raises(DnsError) as blocked:
        strict.accept("service.example", ["10.0.0.1"], DNS_A)
    assert blocked.value.code == "address-policy-private"

    policy = DnsAddressPolicy(rebind_mode="same-class")
    assert policy.accept(
        "service.example", ["192.0.2.8", "198.51.100.8"], DNS_A
    ) == ["192.0.2.8", "198.51.100.8"]
    # Normal public-address rotation may change set cardinality without
    # weakening the address-class boundary.
    assert policy.accept("service.example", ["192.0.2.9"], DNS_A) == [
        "192.0.2.9"
    ]
    with pytest.raises(DnsError) as rebound:
        policy.accept("service.example", ["10.0.0.8"], DNS_A)
    assert rebound.value.code == "dns-rebinding"

    with pytest.raises(DnsError) as mapped_loopback:
        strict.accept("mapped.example", ["::ffff:127.0.0.1"], DNS_AAAA)
    assert mapped_loopback.value.code == "address-policy-loopback"


def test_unrelated_answer_owner_is_not_accepted_as_address() -> None:
    cache = DnsCache(negative_ttl_ms=10)
    operation = Resolver(cache=cache).begin("example.com", DNS_A, 0, 1000)
    unrelated = _a_response(operation.query, owner=encode_name("attacker.example"))
    operation.receive(unrelated, 1)
    assert operation.done
    assert operation.error == "no-address"
    assert operation.values == []


def test_aaaa_reply_and_policy_use_canonical_numeric_values() -> None:
    query = build_query(12, "v6.example", DNS_AAAA)
    address = bytes.fromhex("20010db8000000000000000000000001")
    response = parse_response(_aaaa_response(query, address), 12, "v6.example", DNS_AAAA)
    accepted = DnsAddressPolicy().accept(
        "v6.example", [response.answers[0].data], DNS_AAAA
    )
    assert accepted == ["2001:db8:0:0:0:0:0:1"]


def test_cancel_closes_owned_transport_and_deadline_never_resets() -> None:
    transport = ScriptedTransport()
    resolver = _resolver(transport, attempt_timeout_ms=100)
    driver = resolver.begin_driver("example.com", DNS_A, 0, 250, transport)
    assert driver.step(0).kind == "progress"
    cancelled = driver.cancel()
    assert cancelled.kind == "error" and cancelled.error == "cancelled"
    assert transport.closed == [40]

    transport2 = ScriptedTransport()
    resolver2 = _resolver(
        transport2,
        servers=("192.0.2.53", "198.51.100.53"),
        attempt_timeout_ms=100,
    )
    expiring = resolver2.begin_driver("example.com", DNS_A, 0, 250, transport2)
    retry = expiring.step(100)
    assert retry.kind == "retry"
    assert expiring.attempt_deadline_ms == 200
    assert expiring.step(250).error == "timeout"


def test_tcp_eof_and_retry_exhaustion_are_named_transport_errors() -> None:
    transport = ScriptedTransport()
    resolver = _resolver(transport)
    driver = resolver.begin_driver("example.com", DNS_A, 0, 1000, transport)
    transport.receive_results.append(DnsIoResult(DNS_IO_EOF))
    exhausted = _advance(driver, 1, terminal=("complete", "error"))
    assert exhausted.kind == "error"
    assert exhausted.error == "retry-exhausted:unexpected-eof"


def test_retry_exhausted_attempt_timeout_retains_timeout_cause() -> None:
    transport = ScriptedTransport()
    resolver = _resolver(transport, attempt_timeout_ms=10)
    driver = resolver.begin_driver("example.com", DNS_A, 0, 1000, transport)
    assert driver.step(0).kind == "progress"
    exhausted = driver.step(10)
    assert exhausted.kind == "error"
    assert exhausted.error == "retry-exhausted:attempt-timeout"


def test_explicit_missing_live_transport_fails_closed() -> None:
    resolver = Resolver(config=DnsResolverConfig((DnsServer("192.0.2.53"),), 1))
    driver = resolver.begin_driver("example.com", DNS_A, 0, 1000)
    result = driver.step(0)
    assert result.kind == "error"
    assert result.error == "retry-exhausted:live-dns-transport-unavailable"


@pytest.mark.integration
@pytest.mark.pcc_gate(probe="pcc1")
def test_current_pcc1_self_no_libpython_async_dns_proxy_path(
    tmp_path: Path,
    pcc_py_runtime_archive: Path,
) -> None:
    """Compile/run the pcc-owned driver model below the live adapter gate."""
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the asynchronous DNS gate")
    executable = tmp_path / "current_pcc1_async_dns"
    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    environment["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(PCC1_DNS_SOURCE),
            "-o",
            str(executable),
        ],
        cwd=REPO,
        env=environment,
        text=True,
        capture_output=True,
        timeout=600,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    for backend in range(5):
        run_environment = dict(environment)
        run_environment["PCC_GC_BACKEND"] = str(backend)
        ran = subprocess.run(
            [str(executable)],
            cwd=REPO,
            env=run_environment,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert ran.returncode == 0, (
            "GC" + str(backend) + ": " + ran.stdout + ran.stderr
        )
        assert ran.stdout.strip() == "PCC1_GATEWAY_ASYNC_DNS_MODEL_OK"
