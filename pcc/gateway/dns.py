"""Nonblocking DNS state machine for gateway upstream resolution.

This module is deliberately transport independent.  ``DnsResolveDriver.step``
performs at most one nonblocking transport observation and returns an explicit
read/write wait carrying the *absolute* deadline.  The caller owns waitset
registration and virtual-thread parking; no method here calls ``getaddrinfo``,
``socket`` or another host-Python networking API.

The production adapter lives in :mod:`pcc.gateway.dns_native`: connected UDP
peer provenance, TCP fallback and resolver configuration all cross named pcc
runtime ABIs.  Deterministic and current-pcc1 code can use this same driver
with any transport implementing the small ``open/connect/send/receive/close``
observation contract below.
"""

from threading import Lock

DNS_A = 1
DNS_CNAME = 5
DNS_AAAA = 28
DNS_CLASS_IN = 1

DNS_IO_OK = 0
DNS_IO_WOULD_BLOCK = 1
DNS_IO_EOF = 2
DNS_IO_ERROR = 3

DNS_INTEREST_READ = 1
DNS_INTEREST_WRITE = 2


class DnsError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _u16(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise DnsError("truncated", "truncated DNS u16")
    return data[offset] * 256 + data[offset + 1]


def _u32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise DnsError("truncated", "truncated DNS u32")
    return (
        data[offset] * 16777216
        + data[offset + 1] * 65536
        + data[offset + 2] * 256
        + data[offset + 3]
    )


def _put_u16(output: bytearray, value: int) -> None:
    output.append((value >> 8) & 255)
    output.append(value & 255)


def encode_name(name: str) -> bytes:
    canonical = name.rstrip(".").lower()
    if not canonical or len(canonical) > 253:
        raise DnsError("bad-name", "DNS name length is invalid")
    output = bytearray(b"")
    for label in canonical.split("."):
        try:
            raw = label.encode("ascii")
        except UnicodeError as error:
            raise DnsError("bad-name", "DNS names must be pre-encoded ASCII") from error
        if len(raw) == 0 or len(raw) > 63:
            raise DnsError("bad-name", "DNS label length is invalid")
        if raw[0] == 45 or raw[-1] == 45:
            raise DnsError("bad-name", "DNS label cannot start or end with hyphen")
        for value in raw:
            if not (
                48 <= value <= 57
                or 65 <= value <= 90
                or 97 <= value <= 122
                or value == 45
            ):
                raise DnsError("bad-name", "unsupported DNS label byte")
        output.append(len(raw))
        output.extend(raw)
    output.append(0)
    return bytes(output)


def decode_name(data: bytes, offset: int):
    labels = []
    cursor = offset
    next_offset = -1
    jumps = 0
    visited = []
    while True:
        if cursor >= len(data):
            raise DnsError("truncated", "truncated DNS name")
        length = data[cursor]
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(data):
                raise DnsError("truncated", "truncated DNS compression pointer")
            pointer = ((length & 0x3F) << 8) | data[cursor + 1]
            if pointer >= len(data) or pointer in visited:
                raise DnsError("bad-compression", "invalid DNS compression pointer")
            visited.append(pointer)
            if next_offset < 0:
                next_offset = cursor + 2
            cursor = pointer
            jumps += 1
            if jumps > 32:
                raise DnsError("bad-compression", "DNS compression chain too deep")
            continue
        if length & 0xC0:
            raise DnsError("bad-label", "reserved DNS label encoding")
        cursor += 1
        if length == 0:
            if next_offset < 0:
                next_offset = cursor
            break
        if length > 63 or cursor + length > len(data):
            raise DnsError("truncated", "invalid DNS label length")
        raw = data[cursor:cursor + length]
        try:
            labels.append(raw.decode("ascii").lower())
        except UnicodeError as error:
            raise DnsError("bad-name", "non-ASCII DNS response name") from error
        cursor += length
    return ".".join(labels), next_offset


def build_query(query_id: int, name: str, qtype: int = DNS_A) -> bytes:
    if query_id < 0 or query_id > 65535:
        raise ValueError("DNS query id must fit u16")
    if qtype not in (DNS_A, DNS_AAAA):
        raise ValueError("gateway resolver supports A and AAAA")
    output = bytearray(b"")
    _put_u16(output, query_id)
    _put_u16(output, 0x0100)
    _put_u16(output, 1)
    _put_u16(output, 0)
    _put_u16(output, 0)
    _put_u16(output, 0)
    output.extend(encode_name(name))
    _put_u16(output, qtype)
    _put_u16(output, DNS_CLASS_IN)
    return bytes(output)


class DnsAnswer:
    def __init__(self, name: str, qtype: int, data, ttl: int) -> None:
        self.name = name
        self.qtype = qtype
        self.data = data
        self.ttl = ttl


class DnsResponse:
    def __init__(self, query_id: int, truncated: bool, rcode: int, answers) -> None:
        self.query_id = query_id
        self.truncated = truncated
        self.rcode = rcode
        self.answers = answers


def parse_response(payload: bytes, query_id: int, name: str, qtype: int) -> DnsResponse:
    if len(payload) < 12:
        raise DnsError("truncated", "DNS response header is truncated")
    response_id = _u16(payload, 0)
    flags = _u16(payload, 2)
    if response_id != query_id:
        raise DnsError("id-mismatch", "DNS response id does not match query")
    if flags & 0x8000 == 0:
        raise DnsError("not-response", "DNS packet is not a response")
    if flags & 0x7800:
        raise DnsError("bad-opcode", "DNS response opcode is not QUERY")
    truncated = bool(flags & 0x0200)
    rcode = flags & 15
    qdcount = _u16(payload, 4)
    ancount = _u16(payload, 6)
    if qdcount != 1:
        raise DnsError("question-mismatch", "DNS response must echo one question")
    if ancount > 256:
        raise DnsError("too-many-answers", "DNS answer count exceeds gateway limit")
    offset = 12
    question_name, offset = decode_name(payload, offset)
    question_type = _u16(payload, offset)
    question_class = _u16(payload, offset + 2)
    offset += 4
    if (
        question_name != name.rstrip(".").lower()
        or question_type != qtype
        or question_class != DNS_CLASS_IN
    ):
        raise DnsError("question-mismatch", "DNS echoed question does not match")
    answers = []
    index = 0
    while index < ancount:
        owner, offset = decode_name(payload, offset)
        answer_type = _u16(payload, offset)
        answer_class = _u16(payload, offset + 2)
        ttl = _u32(payload, offset + 4)
        rdlength = _u16(payload, offset + 8)
        rdata_offset = offset + 10
        offset = rdata_offset + rdlength
        if offset > len(payload):
            raise DnsError("truncated", "DNS answer data is truncated")
        if answer_class != DNS_CLASS_IN:
            index += 1
            continue
        if answer_type == DNS_A and rdlength == 4:
            address = (
                str(payload[rdata_offset])
                + "." + str(payload[rdata_offset + 1])
                + "." + str(payload[rdata_offset + 2])
                + "." + str(payload[rdata_offset + 3])
            )
            answers.append(DnsAnswer(owner, answer_type, address, ttl))
        elif answer_type == DNS_AAAA and rdlength == 16:
            groups = []
            group_index = 0
            while group_index < 8:
                value = (
                    payload[rdata_offset + group_index * 2] * 256
                    + payload[rdata_offset + group_index * 2 + 1]
                )
                groups.append(format(value, "x"))
                group_index += 1
            answers.append(DnsAnswer(owner, answer_type, ":".join(groups), ttl))
        elif answer_type == DNS_CNAME:
            cname, cname_end = decode_name(payload, rdata_offset)
            if cname_end > rdata_offset + rdlength:
                raise DnsError("bad-cname", "CNAME exceeds its DNS record")
            answers.append(DnsAnswer(owner, answer_type, cname, ttl))
        index += 1
    return DnsResponse(response_id, truncated, rcode, answers)


def _parse_ipv4(value: str):
    parts = value.split(".")
    if len(parts) != 4:
        return None
    output = []
    for part in parts:
        if not part or len(part) > 3:
            return None
        number = 0
        for byte in part:
            if byte < "0" or byte > "9":
                return None
            number = number * 10 + ord(byte) - 48
        if number > 255:
            return None
        if len(part) > 1 and part[0] == "0":
            return None
        output.append(number)
    return output


def _parse_ipv6_piece(piece: str):
    if not piece or len(piece) > 4:
        return -1
    value = 0
    for char in piece:
        if "0" <= char <= "9":
            digit = ord(char) - 48
        elif "a" <= char <= "f":
            digit = ord(char) - 87
        elif "A" <= char <= "F":
            digit = ord(char) - 55
        else:
            return -1
        value = value * 16 + digit
    return value


def _ipv6_side_groups(side: str, allow_ipv4: bool):
    if side == "":
        return []
    pieces = side.split(":")
    groups = []
    index = 0
    while index < len(pieces):
        piece = pieces[index]
        if "." in piece:
            if not allow_ipv4 or index != len(pieces) - 1:
                return None
            ipv4 = _parse_ipv4(piece)
            if ipv4 is None:
                return None
            groups.append(ipv4[0] * 256 + ipv4[1])
            groups.append(ipv4[2] * 256 + ipv4[3])
        else:
            parsed = _parse_ipv6_piece(piece)
            if parsed < 0:
                return None
            groups.append(parsed)
        index += 1
    return groups


def _parse_ipv6(value: str):
    if not value or "%" in value:
        return None
    if value.count("::") > 1:
        return None
    if "::" in value:
        left_text, right_text = value.split("::")
        left = _ipv6_side_groups(left_text, False)
        right = _ipv6_side_groups(right_text, True)
        if left is None or right is None or len(left) + len(right) >= 8:
            return None
        groups = list(left)
        while len(groups) < 8 - len(right):
            groups.append(0)
        groups.extend(right)
    else:
        groups = _ipv6_side_groups(value, True)
        if groups is None or len(groups) != 8:
            return None
    if len(groups) != 8:
        return None
    output = []
    for group in groups:
        output.append((group >> 8) & 255)
        output.append(group & 255)
    return output


def normalize_numeric_address(value: str, qtype: int = 0):
    """Return one canonical numeric address or ``None`` without host lookup."""
    text = value.strip()
    ipv4 = _parse_ipv4(text)
    if ipv4 is not None and qtype in (0, DNS_A):
        return ".".join(str(part) for part in ipv4)
    ipv6 = _parse_ipv6(text)
    if ipv6 is not None and qtype in (0, DNS_AAAA):
        groups = []
        index = 0
        while index < 16:
            groups.append(format(ipv6[index] * 256 + ipv6[index + 1], "x"))
            index += 2
        return ":".join(groups)
    return None


def _address_class(value: str) -> str:
    ipv4 = _parse_ipv4(value)
    if ipv4 is not None:
        first = ipv4[0]
        second = ipv4[1]
        if first == 0:
            return "unspecified"
        if first == 127:
            return "loopback"
        if first == 10 or (first == 172 and 16 <= second <= 31) or (
            first == 192 and second == 168
        ):
            return "private"
        if first == 169 and second == 254:
            return "link-local"
        if 224 <= first <= 239:
            return "multicast"
        return "public"
    ipv6 = _parse_ipv6(value)
    if ipv6 is None:
        return "invalid"
    # IPv4-mapped IPv6 must inherit the embedded address class; otherwise a
    # textual ``::ffff:127.0.0.1`` could bypass loopback/private policy.
    mapped = True
    index = 0
    while index < 10:
        if ipv6[index] != 0:
            mapped = False
            break
        index += 1
    if mapped and ipv6[10] == 255 and ipv6[11] == 255:
        embedded = (
            str(ipv6[12])
            + "." + str(ipv6[13])
            + "." + str(ipv6[14])
            + "." + str(ipv6[15])
        )
        return _address_class(embedded)
    all_zero = True
    for byte in ipv6:
        if byte != 0:
            all_zero = False
            break
    if all_zero:
        return "unspecified"
    loopback = True
    index = 0
    while index < 15:
        if ipv6[index] != 0:
            loopback = False
            break
        index += 1
    if loopback and ipv6[15] == 1:
        return "loopback"
    if ipv6[0] & 0xFE == 0xFC:
        return "private"
    if ipv6[0] == 0xFE and ipv6[1] & 0xC0 == 0x80:
        return "link-local"
    if ipv6[0] == 0xFF:
        return "multicast"
    return "public"


class DnsAddressPolicy:
    """Address and DNS-rebinding policy applied before values reach a pool.

    ``same-class`` (the default) allows ordinary public/public or
    private/private DNS rotation but rejects a later class transition.  ``pin``
    requires exactly the first accepted set, and ``allow`` disables history
    comparison.  Special address classes still require their explicit flag.
    """

    def __init__(
        self,
        allow_private: bool = True,
        allow_loopback: bool = True,
        allow_link_local: bool = False,
        allow_multicast: bool = False,
        allow_unspecified: bool = False,
        rebind_mode: str = "same-class",
    ) -> None:
        if rebind_mode not in ("allow", "same-class", "pin"):
            raise ValueError("invalid DNS rebinding mode")
        self.allow_private = allow_private
        self.allow_loopback = allow_loopback
        self.allow_link_local = allow_link_local
        self.allow_multicast = allow_multicast
        self.allow_unspecified = allow_unspecified
        self.rebind_mode = rebind_mode
        self._lock = Lock()
        self._bindings = {}

    def accept(self, name: str, values, qtype: int):
        canonical = []
        classes = []
        for value in values:
            normalized = normalize_numeric_address(value, qtype)
            if normalized is None:
                raise DnsError("address-policy", "resolver returned a non-address value")
            kind = _address_class(normalized)
            if kind == "private" and not self.allow_private:
                raise DnsError("address-policy-private", "private DNS address rejected")
            if kind == "loopback" and not self.allow_loopback:
                raise DnsError("address-policy-loopback", "loopback DNS address rejected")
            if kind == "link-local" and not self.allow_link_local:
                raise DnsError("address-policy-link-local", "link-local DNS address rejected")
            if kind == "multicast" and not self.allow_multicast:
                raise DnsError("address-policy-multicast", "multicast DNS address rejected")
            if kind == "unspecified" and not self.allow_unspecified:
                raise DnsError("address-policy-unspecified", "unspecified DNS address rejected")
            if normalized not in canonical:
                canonical.append(normalized)
                if kind not in classes:
                    classes.append(kind)
        if not canonical:
            return canonical
        key = (name.rstrip(".").lower(), qtype)
        current_values = tuple(sorted(canonical))
        current_classes = tuple(sorted(classes))
        self._lock.acquire()
        try:
            previous = self._bindings.get(key)
            if previous is not None:
                if self.rebind_mode == "pin" and previous[0] != current_values:
                    raise DnsError(
                        "dns-rebinding", "DNS address set changed under pin policy"
                    )
                if (
                    self.rebind_mode == "same-class"
                    and previous[1] != current_classes
                ):
                    raise DnsError("dns-rebinding", "DNS address class changed")
            if previous is None or self.rebind_mode != "allow":
                self._bindings[key] = (current_values, current_classes)
        finally:
            self._lock.release()
        return canonical


class HostsTable:
    """Bounded preloaded ``/etc/hosts`` contents; this class performs no I/O."""

    def __init__(self, text: str = "", max_entries: int = 4096) -> None:
        if max_entries <= 0:
            raise ValueError("hosts entry limit must be positive")
        self.max_entries = max_entries
        self.entries = {}
        if text:
            self.load(text)

    def load(self, text: str) -> None:
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if len(line) > 4096:
                raise DnsError("hosts-line-too-long", "hosts line exceeds gateway limit")
            fields = line.split()
            if len(fields) < 2:
                continue
            address = normalize_numeric_address(fields[0])
            if address is None:
                continue
            qtype = DNS_AAAA if ":" in address else DNS_A
            for alias in fields[1:]:
                name = alias.rstrip(".").lower()
                if not name:
                    continue
                key = (name, qtype)
                values = self.entries.get(key)
                if values is None:
                    if len(self.entries) >= self.max_entries:
                        raise DnsError("hosts-too-many-entries", "hosts table exceeds limit")
                    values = []
                    self.entries[key] = values
                if address not in values:
                    values.append(address)

    def lookup(self, name: str, qtype: int):
        values = self.entries.get((name.rstrip(".").lower(), qtype))
        if values is None:
            return None
        return list(values)


class DnsCacheEntry:
    def __init__(
        self,
        values,
        expires_ms: int,
        negative: bool,
        source: str = "dns",
    ) -> None:
        self.values = values
        self.expires_ms = expires_ms
        self.negative = negative
        self.source = source


class DnsCache:
    def __init__(
        self,
        max_entries: int = 1024,
        negative_ttl_ms: int = 5000,
        min_ttl_ms: int = 1000,
        max_ttl_ms: int = 3600000,
    ) -> None:
        if (
            max_entries <= 0
            or negative_ttl_ms <= 0
            or min_ttl_ms < 0
            or max_ttl_ms < min_ttl_ms
        ):
            raise ValueError("invalid DNS cache limits")
        self.max_entries = max_entries
        self.negative_ttl_ms = negative_ttl_ms
        self.min_ttl_ms = min_ttl_ms
        self.max_ttl_ms = max_ttl_ms
        self._lock = Lock()
        self.entries = {}
        self.order = []

    def get(self, name: str, qtype: int, now_ms: int):
        key = (name.rstrip(".").lower(), qtype)
        self._lock.acquire()
        try:
            entry = self.entries.get(key)
            if entry is None:
                return None
            if entry.expires_ms <= now_ms:
                del self.entries[key]
                if key in self.order:
                    self.order.remove(key)
                return None
            if key in self.order:
                self.order.remove(key)
            self.order.append(key)
            return entry
        finally:
            self._lock.release()

    def put(
        self,
        name: str,
        qtype: int,
        values,
        ttl_ms: int,
        now_ms: int,
        negative: bool = False,
        source: str = "dns",
    ):
        key = (name.rstrip(".").lower(), qtype)
        self._lock.acquire()
        try:
            if key in self.entries:
                if key in self.order:
                    self.order.remove(key)
            elif len(self.entries) >= self.max_entries:
                while self.order:
                    oldest = self.order.pop(0)
                    if oldest in self.entries:
                        del self.entries[oldest]
                        break
            self.order.append(key)
            if negative:
                ttl_ms = self.negative_ttl_ms
            else:
                if ttl_ms < self.min_ttl_ms:
                    ttl_ms = self.min_ttl_ms
                if ttl_ms > self.max_ttl_ms:
                    ttl_ms = self.max_ttl_ms
            entry = DnsCacheEntry(
                list(values), now_ms + ttl_ms, negative, source
            )
            self.entries[key] = entry
            return entry
        finally:
            self._lock.release()


def _answer_values(response: DnsResponse, name: str, qtype: int):
    current = name.rstrip(".").lower()
    ttl = 86400
    visited = []
    depth = 0
    while depth < 16:
        values = []
        for answer in response.answers:
            if answer.name == current and answer.qtype == qtype:
                values.append(answer.data)
                if answer.ttl < ttl:
                    ttl = answer.ttl
        if values:
            return values, ttl
        target = ""
        for answer in response.answers:
            if answer.name == current and answer.qtype == DNS_CNAME:
                target = answer.data
                if answer.ttl < ttl:
                    ttl = answer.ttl
                break
        if not target:
            return [], ttl
        if target in visited or target == current:
            raise DnsError("cname-loop", "DNS CNAME chain contains a loop")
        visited.append(current)
        current = target
        depth += 1
    raise DnsError("cname-depth", "DNS CNAME chain exceeds gateway limit")


class ResolveOperation:
    def __init__(
        self,
        query_id: int,
        name: str,
        qtype: int,
        deadline_ms: int,
        cache: DnsCache,
        policy=None,
    ) -> None:
        self.query_id = query_id
        self.name = name.rstrip(".").lower()
        self.qtype = qtype
        self.deadline_ms = deadline_ms
        self.cache = cache
        self.policy = policy or DnsAddressPolicy()
        self.query = build_query(query_id, self.name, qtype)
        self.use_tcp = False
        self.done = False
        self.cancelled = False
        self.values = []
        self.error = ""
        self.source = "dns"

    def cancel(self) -> None:
        if not self.done:
            self.cancelled = True
            self.done = True
            self.error = "cancelled"

    def check_deadline(self, now_ms: int) -> bool:
        if not self.done and not self.cancelled and now_ms >= self.deadline_ms:
            self.error = "timeout"
            self.done = True
            return True
        return False

    def receive(self, payload: bytes, now_ms: int):
        if self.cancelled or self.done:
            raise DnsError("inactive", "DNS operation is no longer active")
        if now_ms >= self.deadline_ms:
            self.check_deadline(now_ms)
            raise DnsError("timeout", "DNS operation deadline expired")
        if self.use_tcp:
            if len(payload) < 2 or _u16(payload, 0) != len(payload) - 2:
                raise DnsError("bad-tcp-frame", "invalid DNS TCP length prefix")
            payload = payload[2:]
        response = parse_response(payload, self.query_id, self.name, self.qtype)
        if response.truncated and not self.use_tcp:
            self.use_tcp = True
            framed = bytearray(b"")
            _put_u16(framed, len(self.query))
            framed.extend(self.query)
            return bytes(framed)
        if response.rcode == 3:
            self.cache.put(self.name, self.qtype, [], 0, now_ms, True)
            self.error = "nxdomain"
            self.done = True
            return None
        if response.rcode != 0:
            raise DnsError("rcode-" + str(response.rcode), "DNS server returned an error")
        values, ttl = _answer_values(response, self.name, self.qtype)
        if not values:
            self.cache.put(self.name, self.qtype, [], 0, now_ms, True)
            self.error = "no-address"
        else:
            values = self.policy.accept(self.name, values, self.qtype)
            self.cache.put(self.name, self.qtype, values, ttl * 1000, now_ms)
            self.values = values
        self.done = True
        return None


class DnsServer:
    def __init__(self, address: str, port: int = 53) -> None:
        canonical = normalize_numeric_address(address)
        if canonical is None:
            raise ValueError("DNS server address must be numeric")
        if port <= 0 or port > 65535:
            raise ValueError("invalid DNS server port")
        self.address = canonical
        self.port = port

    def matches_peer(self, peer) -> bool:
        if peer is None or peer == "":
            return False
        if isinstance(peer, DnsServer):
            return peer.address == self.address and peer.port == self.port
        canonical = normalize_numeric_address(str(peer))
        return canonical == self.address and self.port == 53


class DnsResolverConfig:
    def __init__(
        self,
        servers=(),
        attempts_per_server: int = 2,
        attempt_timeout_ms: int = 1000,
        max_invalid_replies: int = 4,
        max_udp_bytes: int = 4096,
        max_tcp_bytes: int = 65535,
        rotate: bool = False,
        use_tcp: bool = False,
    ) -> None:
        if (
            attempts_per_server <= 0
            or attempt_timeout_ms <= 0
            or max_invalid_replies <= 0
            or max_udp_bytes < 512
            or max_tcp_bytes < 512
            or max_tcp_bytes > 65535
        ):
            raise ValueError("invalid DNS resolver limits")
        normalized = []
        for server in servers:
            if isinstance(server, DnsServer):
                normalized.append(server)
            else:
                normalized.append(DnsServer(str(server)))
        self.servers = tuple(normalized)
        self.attempts_per_server = attempts_per_server
        self.attempt_timeout_ms = attempt_timeout_ms
        self.max_invalid_replies = max_invalid_replies
        self.max_udp_bytes = max_udp_bytes
        self.max_tcp_bytes = max_tcp_bytes
        self.rotate = bool(rotate)
        self.use_tcp = bool(use_tcp)


def _bounded_resolver_option(value: str, minimum: int, maximum: int):
    if not value:
        return None
    parsed = 0
    for char in value:
        if char < "0" or char > "9":
            return None
        parsed = parsed * 10 + ord(char) - 48
        if parsed > maximum:
            return maximum
    if parsed < minimum:
        return minimum
    return parsed


def parse_resolver_config(
    text: str,
    default_attempts: int = 2,
    default_timeout_ms: int = 1000,
) -> DnsResolverConfig:
    """Parse the bounded resolver subset needed by the live gateway.

    Only numeric ``nameserver`` records are accepted.  This is deliberate:
    resolving the resolver would recurse back into the host resolver.  The
    supported ``options`` are ``attempts:N``, ``timeout:N`` (seconds),
    ``rotate`` and ``use-vc``/``usevc``.  Search/domain/ndots affect name
    expansion, which the gateway intentionally does not perform; upstream
    names must be absolute policy inputs even when their trailing dot is
    omitted.
    """
    if len(text) > 65535:
        raise DnsError(
            "resolver-config-too-large",
            "resolver configuration exceeds gateway limit",
        )
    servers = []
    attempts = default_attempts
    timeout_ms = default_timeout_ms
    rotate = False
    use_tcp = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].split(";", 1)[0].strip()
        if not line:
            continue
        if len(line) > 4096:
            raise DnsError(
                "resolver-line-too-long",
                "resolver configuration line exceeds gateway limit",
            )
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "nameserver" and len(fields) >= 2:
            address = normalize_numeric_address(fields[1])
            if address is None:
                # A hostname here would require a resolver to find the
                # resolver.  Ignore it and fail closed if no numeric server
                # remains.
                continue
            duplicate = False
            for server in servers:
                if server.address == address and server.port == 53:
                    duplicate = True
                    break
            if not duplicate and len(servers) < 8:
                servers.append(DnsServer(address))
        elif fields[0] == "options":
            for option in fields[1:]:
                if option == "rotate":
                    rotate = True
                elif option == "use-vc" or option == "usevc":
                    use_tcp = True
                elif option.startswith("attempts:"):
                    parsed = _bounded_resolver_option(
                        option[len("attempts:"):], 1, 5
                    )
                    if parsed is not None:
                        attempts = parsed
                elif option.startswith("timeout:"):
                    parsed = _bounded_resolver_option(
                        option[len("timeout:"):], 1, 30
                    )
                    if parsed is not None:
                        timeout_ms = parsed * 1000
    return DnsResolverConfig(
        servers,
        attempts_per_server=attempts,
        attempt_timeout_ms=timeout_ms,
        rotate=rotate,
        use_tcp=use_tcp,
    )


class DnsIoResult:
    """One nonblocking transport observation returned to the driver."""

    def __init__(
        self,
        status: int,
        handle=-1,
        count: int = 0,
        data: bytes = b"",
        peer=None,
        error: str = "",
    ) -> None:
        self.status = status
        self.handle = handle
        self.count = count
        self.data = data
        self.peer = peer
        self.error = error


class DnsDriveResult:
    """A progress, readiness, retry or terminal result from one driver step."""

    def __init__(
        self,
        kind: str,
        handle=-1,
        interest: int = 0,
        deadline_ms: int = -1,
        server=None,
        values=None,
        source: str = "",
        error: str = "",
    ) -> None:
        self.kind = kind
        self.handle = handle
        self.interest = interest
        self.deadline_ms = deadline_ms
        self.server = server
        self.values = list(values or [])
        self.source = source
        self.error = error


class FailClosedDnsTransport:
    """Explicit fail-closed boundary when no live adapter was supplied.

    This intentionally does not import host ``socket`` or call libc resolver
    functions.  Production gateway construction installs ``NativeDnsTransport``;
    direct resolver users must opt into that boundary rather than silently
    borrowing host networking.
    """

    reason = "live-dns-transport-unavailable"

    def open(self, protocol: str, server: DnsServer) -> DnsIoResult:
        return DnsIoResult(DNS_IO_ERROR, error=self.reason)

    def connect(self, handle, server: DnsServer) -> DnsIoResult:
        return DnsIoResult(DNS_IO_ERROR, handle=handle, error=self.reason)

    def send(self, handle, data: bytes, offset: int) -> DnsIoResult:
        return DnsIoResult(DNS_IO_ERROR, handle=handle, error=self.reason)

    def receive(self, handle, max_bytes: int) -> DnsIoResult:
        return DnsIoResult(DNS_IO_ERROR, handle=handle, error=self.reason)

    def close(self, handle) -> None:
        return None


class DnsResolveDriver:
    """Transport-independent UDP/TCP resolver driver.

    The adapter methods must be immediate observations.  ``WOULD_BLOCK`` maps
    to a returned wait result; callers register ``handle`` and ``interest`` on
    the gateway waitset with the supplied absolute deadline, park, and call
    :meth:`step` again after wakeup.  Partial TCP writes/reads are retained.
    UDP writes must be atomic and replies must come from a connected or
    explicitly identified configured server.
    """

    def __init__(
        self,
        operation,
        transport,
        config: DnsResolverConfig,
        now_ms: int,
        start_server: int = 0,
        terminal_values=None,
        terminal_source: str = "",
        terminal_error: str = "",
    ) -> None:
        self.operation = operation
        self.transport = transport
        self.config = config
        self.start_server = start_server
        self.attempt = 0
        self.max_attempts = len(config.servers) * config.attempts_per_server
        self.attempt_deadline_ms = now_ms
        self.server = None
        self.protocol = "tcp" if config.use_tcp else "udp"
        self.handle = -1
        self.state = "open"
        self.send_data = operation.query if operation is not None else b""
        if operation is not None and config.use_tcp:
            framed_query = bytearray(b"")
            _put_u16(framed_query, len(operation.query))
            framed_query.extend(operation.query)
            self.send_data = bytes(framed_query)
        self.send_offset = 0
        self.tcp_buffer = b""
        self.tcp_expected = -1
        self.invalid_replies = 0
        self.done = terminal_values is not None or bool(terminal_error)
        self.values = list(terminal_values or [])
        self.source = terminal_source
        self.error = terminal_error
        self.last_error = ""
        if not self.done and self.max_attempts == 0:
            self.done = True
            self.error = "no-dns-servers"
        if not self.done:
            self._select_attempt(now_ms)

    def _select_attempt(self, now_ms: int) -> None:
        index = (
            self.start_server
            + self.attempt // self.config.attempts_per_server
        ) % len(self.config.servers)
        self.server = self.config.servers[index]
        self.attempt_deadline_ms = now_ms + self.config.attempt_timeout_ms
        if self.attempt_deadline_ms > self.operation.deadline_ms:
            self.attempt_deadline_ms = self.operation.deadline_ms
        self.protocol = "tcp" if self.config.use_tcp else "udp"
        self.state = "open"
        self.handle = -1
        self.send_data = self.operation.query
        if self.config.use_tcp:
            framed_query = bytearray(b"")
            _put_u16(framed_query, len(self.operation.query))
            framed_query.extend(self.operation.query)
            self.send_data = bytes(framed_query)
        self.send_offset = 0
        self.tcp_buffer = b""
        self.tcp_expected = -1
        self.invalid_replies = 0

    def _advance_attempt_deadline(self, now_ms: int) -> None:
        self.attempt_deadline_ms = now_ms + self.config.attempt_timeout_ms
        if self.attempt_deadline_ms > self.operation.deadline_ms:
            self.attempt_deadline_ms = self.operation.deadline_ms

    def _close(self) -> None:
        if self.handle != -1:
            self.transport.close(self.handle)
            self.handle = -1

    def _terminal(self, error: str = "") -> DnsDriveResult:
        self._close()
        self.done = True
        if error:
            self.error = error
            if self.operation is not None and not self.operation.done:
                self.operation.error = error
                self.operation.done = True
        elif self.operation is not None:
            self.values = list(self.operation.values)
            self.source = self.operation.source
            self.error = self.operation.error
        if self.error:
            return DnsDriveResult("error", values=self.values, source=self.source, error=self.error)
        return DnsDriveResult("complete", values=self.values, source=self.source)

    def _retry(self, error: str, now_ms: int) -> DnsDriveResult:
        self._close()
        self.last_error = error
        self.attempt += 1
        if now_ms >= self.operation.deadline_ms:
            return self._terminal("timeout")
        if self.attempt >= self.max_attempts:
            return self._terminal("retry-exhausted:" + error)
        self._select_attempt(now_ms)
        return DnsDriveResult("retry", server=self.server, error=error)

    def _wait(self, interest: int) -> DnsDriveResult:
        kind = "wait-read" if interest == DNS_INTEREST_READ else "wait-write"
        return DnsDriveResult(
            kind,
            handle=self.handle,
            interest=interest,
            deadline_ms=self.attempt_deadline_ms,
            server=self.server,
        )

    def _invalid(self, error: DnsError, now_ms: int, tcp: bool) -> DnsDriveResult:
        self.invalid_replies += 1
        if (
            tcp
            or error.code.startswith("rcode-")
            or self.invalid_replies >= self.config.max_invalid_replies
        ):
            return self._retry(error.code, now_ms)
        return DnsDriveResult("ignored", server=self.server, error=error.code)

    def _switch_to_tcp(self, framed_query: bytes, now_ms: int) -> DnsDriveResult:
        self._close()
        self.protocol = "tcp"
        self.state = "open"
        self.send_data = framed_query
        self.send_offset = 0
        self.tcp_buffer = b""
        self.tcp_expected = -1
        self.attempt_deadline_ms = now_ms + self.config.attempt_timeout_ms
        if self.attempt_deadline_ms > self.operation.deadline_ms:
            self.attempt_deadline_ms = self.operation.deadline_ms
        return DnsDriveResult("progress", server=self.server)

    def cancel(self) -> DnsDriveResult:
        if self.done:
            return self._terminal(self.error)
        if self.operation is not None:
            self.operation.cancel()
        return self._terminal("cancelled")

    def step(self, now_ms: int) -> DnsDriveResult:
        if self.done:
            if self.error:
                return DnsDriveResult(
                    "error", values=self.values, source=self.source, error=self.error
                )
            return DnsDriveResult("complete", values=self.values, source=self.source)
        if self.operation.cancelled:
            return self._terminal("cancelled")
        if now_ms >= self.operation.deadline_ms:
            return self._terminal("timeout")
        if now_ms >= self.attempt_deadline_ms:
            return self._retry("attempt-timeout", now_ms)

        if self.state == "open":
            observed = self.transport.open(self.protocol, self.server)
            if observed.status != DNS_IO_OK or observed.handle == -1:
                error = observed.error or "transport-open"
                return self._retry(error, now_ms)
            self.handle = observed.handle
            self.state = "connect"
            return DnsDriveResult("progress", server=self.server)

        if self.state == "connect":
            observed = self.transport.connect(self.handle, self.server)
            if observed.status == DNS_IO_WOULD_BLOCK:
                return self._wait(DNS_INTEREST_WRITE)
            if observed.status != DNS_IO_OK:
                return self._retry(observed.error or "transport-connect", now_ms)
            self.state = "send"
            return DnsDriveResult("progress", server=self.server)

        if self.state == "send":
            observed = self.transport.send(self.handle, self.send_data, self.send_offset)
            if observed.status == DNS_IO_WOULD_BLOCK:
                return self._wait(DNS_INTEREST_WRITE)
            if observed.status != DNS_IO_OK:
                return self._retry(observed.error or "transport-send", now_ms)
            remaining = len(self.send_data) - self.send_offset
            if observed.count <= 0 or observed.count > remaining:
                return self._retry("invalid-send-progress", now_ms)
            if self.protocol == "udp" and observed.count != remaining:
                return self._retry("short-udp-send", now_ms)
            self.send_offset += observed.count
            if self.send_offset == len(self.send_data):
                self.state = "receive"
            return DnsDriveResult("progress", server=self.server)

        observed = self.transport.receive(
            self.handle,
            self.config.max_udp_bytes if self.protocol == "udp" else self.config.max_tcp_bytes,
        )
        if observed.status == DNS_IO_WOULD_BLOCK:
            return self._wait(DNS_INTEREST_READ)
        if observed.status == DNS_IO_EOF:
            return self._retry("unexpected-eof", now_ms)
        if observed.status != DNS_IO_OK:
            return self._retry(observed.error or "transport-receive", now_ms)
        if not observed.data:
            return self._retry("empty-response", now_ms)
        receive_limit = (
            self.config.max_udp_bytes
            if self.protocol == "udp"
            else self.config.max_tcp_bytes
        )
        if len(observed.data) > receive_limit:
            return self._retry("response-too-large", now_ms)

        if self.protocol == "udp":
            if not self.server.matches_peer(observed.peer):
                return self._invalid(
                    DnsError("server-mismatch", "DNS reply came from an unconfigured peer"),
                    now_ms,
                    False,
                )
            try:
                framed = self.operation.receive(observed.data, now_ms)
            except DnsError as error:
                return self._invalid(error, now_ms, False)
            if framed is not None:
                return self._switch_to_tcp(framed, now_ms)
            return self._terminal()

        if observed.peer is not None and not self.server.matches_peer(
            observed.peer
        ):
            return self._invalid(
                DnsError(
                    "server-mismatch",
                    "DNS TCP reply came from an unconfigured peer",
                ),
                now_ms,
                True,
            )
        self.tcp_buffer = self.tcp_buffer + observed.data
        if len(self.tcp_buffer) > self.config.max_tcp_bytes + 2:
            return self._retry("response-too-large", now_ms)
        if self.tcp_expected < 0 and len(self.tcp_buffer) >= 2:
            self.tcp_expected = _u16(self.tcp_buffer, 0)
            if self.tcp_expected <= 0 or self.tcp_expected > self.config.max_tcp_bytes:
                return self._retry("bad-tcp-frame-length", now_ms)
        if self.tcp_expected < 0 or len(self.tcp_buffer) < self.tcp_expected + 2:
            # A partial framed TCP reply made real transport progress.  Give
            # the next fragment one bounded idle interval while retaining the
            # operation's immutable global deadline.
            self._advance_attempt_deadline(now_ms)
            return DnsDriveResult("progress", server=self.server)
        frame = self.tcp_buffer[:self.tcp_expected + 2]
        try:
            self.operation.receive(frame, now_ms)
        except DnsError as error:
            return self._invalid(error, now_ms, True)
        return self._terminal()


class Resolver:
    def __init__(
        self,
        cache=None,
        config=None,
        hosts=None,
        policy=None,
        query_seed: int = 1,
    ) -> None:
        if query_seed <= 0 or query_seed > 65535:
            raise ValueError("DNS query seed must fit nonzero u16")
        self.cache = cache or DnsCache()
        self.config = config or DnsResolverConfig()
        self.hosts = hosts or HostsTable()
        self.policy = policy or DnsAddressPolicy()
        self._lock = Lock()
        self.next_query_id = query_seed
        self.next_server = 0

    def _allocate_operation(
        self, name: str, qtype: int, deadline_ms: int
    ) -> ResolveOperation:
        self._lock.acquire()
        try:
            query_id = self.next_query_id
            self.next_query_id = (self.next_query_id + 1) & 65535
            if self.next_query_id == 0:
                self.next_query_id = 1
        finally:
            self._lock.release()
        return ResolveOperation(
            query_id, name, qtype, deadline_ms, self.cache, self.policy
        )

    def begin(self, name: str, qtype: int, now_ms: int, deadline_ms: int):
        """Low-level wire operation retained for sans-transport codec users."""
        cached = self.cache.get(name, qtype, now_ms)
        if cached is not None:
            return cached
        return self._allocate_operation(name, qtype, deadline_ms)

    def begin_driver(
        self,
        name: str,
        qtype: int,
        now_ms: int,
        deadline_ms: int,
        transport=None,
    ) -> DnsResolveDriver:
        """Start numeric -> hosts -> cache -> asynchronous DNS resolution.

        ``deadline_ms`` is absolute and is never recomputed after retry.  With
        no transport argument, unresolved names use the explicit fail-closed
        live boundary rather than host ``getaddrinfo``.
        """
        if qtype not in (DNS_A, DNS_AAAA):
            raise ValueError("gateway resolver supports A and AAAA")
        if deadline_ms <= now_ms:
            operation = self._allocate_operation(name, qtype, deadline_ms)
            operation.check_deadline(now_ms)
            return DnsResolveDriver(
                operation,
                transport or FailClosedDnsTransport(),
                self.config,
                now_ms,
                terminal_error="timeout",
            )
        numeric = normalize_numeric_address(name, qtype)
        if numeric is not None:
            values = self.policy.accept(name, [numeric], qtype)
            return DnsResolveDriver(
                None,
                transport or FailClosedDnsTransport(),
                self.config,
                now_ms,
                terminal_values=values,
                terminal_source="numeric",
            )
        hosts_values = self.hosts.lookup(name, qtype)
        if hosts_values is not None:
            values = self.policy.accept(name, hosts_values, qtype)
            return DnsResolveDriver(
                None,
                transport or FailClosedDnsTransport(),
                self.config,
                now_ms,
                terminal_values=values,
                terminal_source="hosts",
            )
        cached = self.cache.get(name, qtype, now_ms)
        if cached is not None:
            error = "cached-negative" if cached.negative else ""
            return DnsResolveDriver(
                None,
                transport or FailClosedDnsTransport(),
                self.config,
                now_ms,
                terminal_values=[] if cached.negative else cached.values,
                terminal_source="cache",
                terminal_error=error,
            )
        operation = self._allocate_operation(name, qtype, deadline_ms)
        self._lock.acquire()
        try:
            start_server = self.next_server if self.config.rotate else 0
            if self.config.servers and self.config.rotate:
                self.next_server = (self.next_server + 1) % len(
                    self.config.servers
                )
        finally:
            self._lock.release()
        return DnsResolveDriver(
            operation,
            transport or FailClosedDnsTransport(),
            self.config,
            now_ms,
            start_server=start_server,
        )
