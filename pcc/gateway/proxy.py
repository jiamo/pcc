"""Reverse-proxy policy shared by the transport implementation and tests.

This module owns deterministic header sanitation, bounded upstream admission,
selection and retry-before-commit decisions. It intentionally performs no
blocking DNS or socket calls.
"""

from .dns import DnsAddressPolicy
from threading import Lock

_HOP_BY_HOP = (
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
)


def _connection_named_fields(headers):
    named = []
    for name, value in headers:
        if name.lower() == "connection":
            for item in value.split(","):
                token = item.strip().lower()
                if token and token not in named:
                    named.append(token)
    return named


def sanitize_hop_by_hop(headers):
    """Return a new list with RFC hop-by-hop and Connection-named fields removed."""
    connection_fields = _connection_named_fields(headers)
    output = []
    for name, value in headers:
        lower = name.lower()
        if lower in _HOP_BY_HOP or lower in connection_fields:
            continue
        output.append((lower, value))
    return output


def forwarded_request_headers(
    headers,
    client_ip: str,
    scheme: str,
    original_host: str,
    trust_incoming: bool = False,
):
    output = sanitize_hop_by_hop(headers)
    kept = []
    incoming_for = ""
    for name, value in output:
        if name in ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"):
            if trust_incoming and name == "x-forwarded-for":
                incoming_for = value
            if not trust_incoming:
                continue
            if name != "x-forwarded-for":
                continue
        kept.append((name, value))
    chain = client_ip
    if incoming_for:
        chain = incoming_for + ", " + client_ip
    kept.append(("x-forwarded-for", chain))
    kept.append(("x-forwarded-proto", scheme))
    kept.append(("x-forwarded-host", original_host))
    return kept


class UpstreamEndpoint:
    def __init__(
        self,
        host: str,
        port: int,
        weight: int = 1,
        dns_policy=None,
    ) -> None:
        if not host or port <= 0 or port > 65535 or weight <= 0:
            raise ValueError("invalid upstream endpoint")
        self.host = host
        self.port = port
        self.weight = weight
        self.active = 0
        self.failures = 0
        self._state_lock = Lock()
        self.address_cursor = 0
        self.last_address_set = ()
        self.dns_policy = dns_policy or DnsAddressPolicy()
        self._address_lock = Lock()

    def accept_resolved(self, values, qtype: int):
        """Apply endpoint-lived rebinding history across connections."""
        return self.dns_policy.accept(self.host, values, qtype)

    def choose_address(self, values) -> str:
        """Round-robin one policy-approved DNS address.

        The authority remains ``host``; only the connect destination changes.
        A changed DNS set resets the cursor after ``DnsAddressPolicy`` has
        accepted the rebinding transition.  This method never accepts raw DNS
        input directly.
        """
        self._address_lock.acquire()
        try:
            current = tuple(values)
            if not current:
                raise ValueError("upstream endpoint has no resolved address")
            address_set = tuple(sorted(current))
            if address_set != self.last_address_set:
                self.last_address_set = address_set
                self.address_cursor = 0
            address = address_set[self.address_cursor % len(address_set)]
            self.address_cursor = (self.address_cursor + 1) % len(current)
            return address
        finally:
            self._address_lock.release()

    def ordered_addresses(self, values):
        """Return every address once, beginning at the rotation cursor."""
        self._address_lock.acquire()
        try:
            return self._ordered_addresses_unlocked(values)
        finally:
            self._address_lock.release()

    def _ordered_addresses_unlocked(self, values):
        current = tuple(values)
        if not current:
            return ()
        address_set = tuple(sorted(current))
        if address_set != self.last_address_set:
            self.last_address_set = address_set
            self.address_cursor = 0
        output = []
        index = 0
        while index < len(address_set):
            output.append(
                address_set[(self.address_cursor + index) % len(address_set)]
            )
            index += 1
        self.address_cursor = (self.address_cursor + 1) % len(address_set)
        return tuple(output)

    def accept_and_order_addresses(self, values, qtype: int):
        """Atomically apply rebinding history and advance this endpoint."""
        self._address_lock.acquire()
        try:
            accepted = self.dns_policy.accept(self.host, values, qtype)
            return self._ordered_addresses_unlocked(accepted)
        finally:
            self._address_lock.release()

    def try_acquire(self, group_active: int) -> bool:
        """Reserve this endpoint even when it is shared by several groups."""
        self._state_lock.acquire()
        try:
            if self.active > group_active + self.weight:
                return False
            self.active += 1
            return True
        finally:
            self._state_lock.release()

    def release(self, failed: bool = False) -> None:
        self._state_lock.acquire()
        try:
            if self.active <= 0:
                raise RuntimeError("upstream endpoint admission underflow")
            self.active -= 1
            if failed:
                self.failures += 1
        finally:
            self._state_lock.release()


class UpstreamLease:
    def __init__(self, group, endpoint: UpstreamEndpoint) -> None:
        self.group = group
        self.endpoint = endpoint
        self.released = False

    def release(self, failed: bool = False) -> None:
        self.group.release_lease(self, failed)


class UpstreamGroup:
    def __init__(self, name: str, endpoints, max_active: int = 1024, max_idle: int = 128) -> None:
        if not name or not endpoints:
            raise ValueError("upstream group requires a name and endpoint")
        if max_active <= 0 or max_idle < 0:
            raise ValueError("invalid upstream pool limits")
        self.name = name
        self.endpoints = list(endpoints)
        self.max_active = max_active
        # An idle connection retains its active lease, so max_active is
        # already the hard upper bound on the idle population.  Accept a
        # larger max_idle (including the default when callers deliberately
        # choose a small max_active); it is redundant, not unsafe.
        self.max_idle = max_idle
        self._lock = Lock()
        self.active = 0
        self.cursor = 0

    def acquire(self):
        self._lock.acquire()
        try:
            if self.active >= self.max_active:
                return None
            count = len(self.endpoints)
            attempts = 0
            while attempts < count:
                endpoint = self.endpoints[self.cursor]
                self.cursor = (self.cursor + 1) % count
                attempts += 1
                # Endpoint weights affect repeated selection without allowing
                # one endpoint to bypass group-wide admission.
                if endpoint.try_acquire(self.active):
                    self.active += 1
                    return UpstreamLease(self, endpoint)
            return None
        finally:
            self._lock.release()

    def release_lease(self, lease, failed: bool = False) -> None:
        self._lock.acquire()
        try:
            if lease.group is not self or lease.released:
                raise RuntimeError("upstream lease released more than once")
            lease.released = True
            lease.endpoint.release(failed)
            self.active -= 1
        finally:
            self._lock.release()

    def saturated(self) -> bool:
        self._lock.acquire()
        try:
            return self.active >= self.max_active
        finally:
            self._lock.release()

    def accept_and_order_addresses(self, endpoint, values, qtype: int):
        """Own rebinding history and rotation across every pool/server."""
        self._lock.acquire()
        try:
            if endpoint not in self.endpoints:
                raise RuntimeError("upstream endpoint does not belong to group")
            return endpoint.accept_and_order_addresses(values, qtype)
        finally:
            self._lock.release()


class ProxyTimeouts:
    def __init__(
        self,
        connect_ms: int = 5000,
        header_ms: int = 10000,
        body_ms: int = 30000,
        idle_ms: int = 30000,
    ) -> None:
        for value in (connect_ms, header_ms, body_ms, idle_ms):
            if value <= 0:
                raise ValueError("proxy timeouts must be positive")
        self.connect_ms = connect_ms
        self.header_ms = header_ms
        self.body_ms = body_ms
        self.idle_ms = idle_ms


class RetryPolicy:
    def __init__(self, attempts: int = 1, methods=("GET", "HEAD", "OPTIONS")) -> None:
        if attempts < 1:
            raise ValueError("proxy attempts must be at least one")
        self.attempts = attempts
        self.methods = tuple(method.upper() for method in methods)

    def allows(
        self,
        method: str,
        attempt: int,
        response_committed: bool,
        failure: str,
        request_replayable: bool = True,
    ) -> bool:
        if not request_replayable or response_committed or attempt >= self.attempts:
            return False
        if method.upper() not in self.methods:
            return False
        return failure in (
            "dns",
            "dns-timeout",
            "connect",
            "connect-timeout",
            "header-timeout",
            "reset-before-head",
            "timeout-before-head",
            "refused",
        )


class ProxySpec:
    def __init__(
        self,
        upstream: str,
        strip_prefix: str = "",
        timeouts=None,
        retry=None,
        trust_forwarded: bool = False,
    ) -> None:
        if not upstream:
            raise ValueError("proxy route requires an upstream group")
        self.upstream = upstream
        self.strip_prefix = strip_prefix
        self.timeouts = timeouts or ProxyTimeouts()
        self.retry = retry or RetryPolicy()
        self.trust_forwarded = trust_forwarded


class ProxyTransportPlan:
    """Marker for a routed request that still needs an owned proxy transport.

    ``pcc.web`` subclasses this record for dispatch planning.  Keeping the
    marker in the lower gateway layer lets the server fail closed without a
    reverse import of ``pcc.web.app`` (which would create a pcc1 closure cycle).
    """

    pass


def proxy_failure_status(failure: str) -> int:
    if failure in (
        "dns-timeout",
        "connect-timeout",
        "request-body-timeout",
        "header-timeout",
        "body-timeout",
    ):
        return 408 if failure == "request-body-timeout" else 504
    if failure in ("overloaded", "no-endpoint"):
        return 503
    return 502
