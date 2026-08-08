"""Finite nonblocking TLS provider ABI for :mod:`pcc.gateway`.

The gateway owns scheduling, deadlines, listener lifecycle and HTTP.  A named
provider owns only TLS record processing, certificate/key parsing and crypto.
There is deliberately no implicit ``ssl``, ``asyncio`` or blocking fallback.

Provider ABI v1
---------------

Providers expose immutable metadata plus this finite method surface::

    create_server_context(config, certificate) -> opaque context
    new_connection(default_context, nonblocking_fd) -> opaque session
    handshake(session) -> TlsResult
    set_server_context(session, selected_context) -> TlsResult(TLS_OK)
    selected_alpn(session) -> str
    read(session, output, limit) -> TlsResult
    write(session, data, length) -> TlsResult
    close_notify(session) -> TlsResult
    free_connection(session) -> None
    free_context(context) -> None

``handshake`` may return ``TLS_SELECT_SNI`` once, with ``server_name`` set.
``TlsChannel`` selects a context from its immutable generation and installs it;
the caller then invokes ``handshake`` again without parking.  All other
incomplete operations return ``TLS_WANT_READ`` or ``TLS_WANT_WRITE``.  The
provider never waits, changes socket flags, calls the scheduler or owns an HTTP
object.

``PccNativeTlsProvider`` is the production adapter.  It talks to one reviewed
native crypto provider through ``pcc_tls_provider_v1_call`` loaded from an
explicit shared-library path.  The adapter probes all required capabilities
before accepting a certificate generation.  It never imports Python ``ssl``
and a missing, partial or older provider fails listener startup instead of
falling back to a host interpreter or plaintext.
"""

import os
from threading import Lock

from pcc.extern import c_int64, c_ptr, extern
from pcc.py_runtime.py.py_abi_constants import (
    PYBYTEARRAYOBJECT_DATA_OFFSET,
    PYBYTESOBJECT_DATA_OFFSET,
)
from pcc.unsafe import (
    call_i64_i64_ptr,
    cstr,
    dynamic_library_close,
    dynamic_library_open,
    dynamic_library_symbol,
    load_i64,
    load_ptr,
    ptr_add,
    ptr_to_int,
    stack_alloc,
    store_i64,
    store_ptr,
)
from .config import TLS_PROVIDER_DEFAULT_MAX_BYTES, _valid_sha256_hex

TLS_PROVIDER_ABI_VERSION = 1

TLS_OK = 0
TLS_WANT_READ = 1
TLS_WANT_WRITE = 2
TLS_CLOSED = 3
TLS_SELECT_SNI = 4
TLS_ERROR = -1

TLS_INTEREST_NONE = 0
TLS_INTEREST_READ = 1
TLS_INTEREST_WRITE = 2

TLS_ERR_NONE = 0
TLS_ERR_DEADLINE = 1
TLS_ERR_CANCELLED = 2
TLS_ERR_PROTOCOL = 3
TLS_ERR_CERTIFICATE = 4
TLS_ERR_IO = 5
TLS_ERR_TRUNCATED = 6
TLS_ERR_UNRECOGNIZED_NAME = 7
TLS_ERR_ALPN = 8
TLS_ERR_PROVIDER = 9
TLS_ERR_PROVIDER_CONTRACT = 10
TLS_ERR_CONFIGURATION = 11
TLS_ERR_INTERNAL = 12

_TLS_MAX_DETAIL = 160
_TLS_MAX_PROVIDER_NAME = 64
_TLS_MAX_SNI_NAME = 253
_TLS_MAX_CERTIFICATES = 64
_TLS_MAX_ALPN_PROTOCOLS = 16
_TLS_MAX_PROVIDER_PATH = 4096

PCC_NATIVE_TLS_PROVIDER_NAME = "pcc-native-tls-v1"
PCC_NATIVE_TLS_ENTRY_SYMBOL = "pcc_tls_provider_v1_call"
PCC_NATIVE_TLS_ABI_VERSION = 1
PCC_NATIVE_TLS_ABI_NAME = "pcc-tls-native-provider-v1"

# The native provider exposes one deliberately small dispatcher.  Every call
# receives an operation number and a pointer to ``pcc_tls_provider_v1_request``
# (specified in pcc/gateway/include/pcc_tls_provider_v1.h).  A single fixed ABI
# avoids compiler-owned knowledge of OpenSSL/BoringSSL struct layouts.
PCC_TLS_OP_PROBE = 0
PCC_TLS_OP_CONTEXT_CREATE = 1
PCC_TLS_OP_CONTEXT_FREE = 2
PCC_TLS_OP_CONNECTION_CREATE = 3
PCC_TLS_OP_CONNECTION_FREE = 4
PCC_TLS_OP_HANDSHAKE = 5
PCC_TLS_OP_SET_CONTEXT = 6
PCC_TLS_OP_SELECTED_ALPN = 7
PCC_TLS_OP_READ = 8
PCC_TLS_OP_WRITE = 9
PCC_TLS_OP_CLOSE_NOTIFY = 10

PCC_TLS_CAP_TLS12 = 1 << 0
PCC_TLS_CAP_TLS13 = 1 << 1
PCC_TLS_CAP_CERTIFICATE_CHAIN = 1 << 2
PCC_TLS_CAP_PRIVATE_KEY = 1 << 3
PCC_TLS_CAP_SNI = 1 << 4
PCC_TLS_CAP_ALPN = 1 << 5
PCC_TLS_CAP_NONBLOCKING = 1 << 6
PCC_TLS_CAP_CLOSE_NOTIFY = 1 << 7
PCC_TLS_CAP_CLIENT_CERTIFICATE = 1 << 8
PCC_TLS_REQUIRED_CAPABILITIES = (
    PCC_TLS_CAP_TLS12
    | PCC_TLS_CAP_TLS13
    | PCC_TLS_CAP_CERTIFICATE_CHAIN
    | PCC_TLS_CAP_PRIVATE_KEY
    | PCC_TLS_CAP_SNI
    | PCC_TLS_CAP_ALPN
    | PCC_TLS_CAP_NONBLOCKING
    | PCC_TLS_CAP_CLOSE_NOTIFY
)

# Fixed request layout, in 64-bit words.  Pointer-width is part of the v1 ABI;
# current pcc production targets are AArch64 Darwin and x86_64 Linux.
_PCC_TLS_REQUEST_BYTES = 160
_PCC_TLS_REQ_SIZE = 0
_PCC_TLS_REQ_ABI = 8
_PCC_TLS_REQ_OPERATION = 16
_PCC_TLS_REQ_STATUS = 24
_PCC_TLS_REQ_ERROR = 32
_PCC_TLS_REQ_PRIMARY = 40
_PCC_TLS_REQ_SECONDARY = 48
_PCC_TLS_REQ_INPUT0 = 56
_PCC_TLS_REQ_INPUT0_LEN = 64
_PCC_TLS_REQ_INPUT1 = 72
_PCC_TLS_REQ_INPUT1_LEN = 80
_PCC_TLS_REQ_INPUT2 = 88
_PCC_TLS_REQ_INPUT2_LEN = 96
_PCC_TLS_REQ_OUTPUT0 = 104
_PCC_TLS_REQ_OUTPUT0_CAP = 112
_PCC_TLS_REQ_OUTPUT0_LEN = 120
_PCC_TLS_REQ_FLAGS = 128
_PCC_TLS_REQ_PROVIDER_CODE = 136
_PCC_TLS_REQ_INPUT3 = 144
_PCC_TLS_REQ_INPUT3_LEN = 152

_py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
_py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
_py_str_byte_len = extern("py_str_byte_len", (c_ptr,), c_int64)


class TlsProviderError(RuntimeError):
    """Configuration/ownership failure outside the nonblocking data path."""


def tls_error_name(error_code: int) -> str:
    """Return the stable public name for a TLS error code."""

    if error_code == TLS_ERR_NONE:
        return "none"
    if error_code == TLS_ERR_DEADLINE:
        return "deadline"
    if error_code == TLS_ERR_CANCELLED:
        return "cancelled"
    if error_code == TLS_ERR_PROTOCOL:
        return "protocol"
    if error_code == TLS_ERR_CERTIFICATE:
        return "certificate"
    if error_code == TLS_ERR_IO:
        return "io"
    if error_code == TLS_ERR_TRUNCATED:
        return "truncated"
    if error_code == TLS_ERR_UNRECOGNIZED_NAME:
        return "unrecognized-name"
    if error_code == TLS_ERR_ALPN:
        return "alpn"
    if error_code == TLS_ERR_PROVIDER:
        return "provider"
    if error_code == TLS_ERR_PROVIDER_CONTRACT:
        return "provider-contract"
    if error_code == TLS_ERR_CONFIGURATION:
        return "configuration"
    if error_code == TLS_ERR_INTERNAL:
        return "internal"
    return "unknown"


def _bounded_detail(detail) -> str:
    if detail is None:
        return ""
    value = str(detail)
    if len(value) > _TLS_MAX_DETAIL:
        return value[:_TLS_MAX_DETAIL]
    return value


class TlsResult:
    """One provider operation result with bounded diagnostics.

    ``detail`` is local diagnostic context only.  Protocol responses and public
    metrics use ``error_code``/``error_name`` so provider strings cannot become
    an unstable or secret-bearing wire surface.
    """

    def __init__(
        self,
        status: int,
        count: int = 0,
        error_code: int = TLS_ERR_NONE,
        detail: str = "",
        server_name: str = "",
    ) -> None:
        self.status = status
        self.count = count
        self.error_code = error_code
        self.detail = _bounded_detail(detail)
        self.server_name = server_name

    @property
    def error_name(self) -> str:
        return tls_error_name(self.error_code)

    @property
    def wait_interest(self) -> int:
        if self.status == TLS_WANT_READ:
            return TLS_INTEREST_READ
        if self.status == TLS_WANT_WRITE:
            return TLS_INTEREST_WRITE
        return TLS_INTEREST_NONE


def _tls_error(error_code: int, detail: str = "") -> TlsResult:
    return TlsResult(TLS_ERROR, 0, error_code, detail)


def _valid_provider_name(name: str) -> bool:
    if not name or len(name) > _TLS_MAX_PROVIDER_NAME:
        return False
    for character in name:
        if not (
            "a" <= character <= "z"
            or "0" <= character <= "9"
            or character in ("-", "_", ".")
        ):
            return False
    return True


def _normalize_sni_name(server_name: str, allow_wildcard: bool = False) -> str:
    if not isinstance(server_name, str):
        raise ValueError("SNI name must be a string")
    name = server_name.rstrip(".").lower()
    if not name or len(name) > _TLS_MAX_SNI_NAME:
        raise ValueError("SNI name is empty or too long")
    try:
        name.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("SNI name must be ASCII") from error
    if "*" in name:
        if not allow_wildcard:
            raise ValueError("client SNI cannot be a wildcard")
        if not name.startswith("*.") or name.count("*") != 1:
            raise ValueError(
                "wildcard SNI must use one leading '*.' label"
            )
    wildcard = False
    if name.startswith("*."):
        wildcard = True
        name_without_wildcard = name[2:]
    else:
        name_without_wildcard = name
    labels = name_without_wildcard.split(".")
    if wildcard and len(labels) < 2:
        raise ValueError("wildcard SNI must contain a registrable suffix")
    for label in labels:
        if not label or len(label) > 63 or label[0] == "-" or label[-1] == "-":
            raise ValueError("invalid SNI label")
        for character in label:
            if not (
                "a" <= character <= "z"
                or "0" <= character <= "9"
                or character == "-"
            ):
                raise ValueError("invalid SNI character")
    if wildcard:
        return "*." + name_without_wildcard
    return name_without_wildcard


def _validate_alpn(protocols) -> tuple:
    result = []
    if len(protocols) > _TLS_MAX_ALPN_PROTOCOLS:
        raise ValueError("too many ALPN protocols")
    for protocol in protocols:
        if not isinstance(protocol, str) or not protocol or len(protocol) > 255:
            raise ValueError("invalid ALPN protocol")
        try:
            protocol.encode("ascii")
        except UnicodeEncodeError as error:
            raise ValueError("ALPN protocol must be ASCII") from error
        if protocol in result:
            raise ValueError("duplicate ALPN protocol")
        result.append(protocol)
    return tuple(result)


class TlsCertificate:
    """Opaque certificate/key inputs and the SNI names selecting them."""

    def __init__(
        self,
        identifier: str,
        certificate: str,
        private_key: str,
        server_names=(),
    ) -> None:
        if not identifier or len(identifier) > 64:
            raise ValueError("TLS certificate identifier is missing or too long")
        if not certificate or not private_key:
            raise ValueError("TLS certificate and private key are required")
        normalized_names = []
        for server_name in server_names:
            normalized = _normalize_sni_name(server_name, True)
            if normalized in normalized_names:
                raise ValueError("duplicate SNI name in certificate")
            normalized_names.append(normalized)
        self.identifier = identifier
        self.certificate = certificate
        self.private_key = private_key
        self.server_names = tuple(normalized_names)


class TlsConfig:
    """One immutable-generation TLS policy.

    Certificate and key values are opaque provider inputs: paths, native
    handles or in-memory references are provider-specific.  The gateway never
    parses or logs them.
    """

    def __init__(
        self,
        certificate: str = "",
        private_key: str = "",
        alpn=("http/1.1",),
        require_client_certificate: bool = False,
        sni_certificates=(),
        reject_unknown_sni: bool = False,
        default_certificate=None,
        client_ca: str = "",
    ) -> None:
        if default_certificate is not None and (certificate or private_key):
            raise ValueError("choose default_certificate or certificate/private_key")
        if default_certificate is None:
            default_certificate = TlsCertificate(
                "default", certificate, private_key, ()
            )
        if not isinstance(default_certificate, TlsCertificate):
            raise TypeError("default TLS certificate must be TlsCertificate")
        if len(sni_certificates) > _TLS_MAX_CERTIFICATES - 1:
            raise ValueError("too many SNI certificates")
        identifiers = [default_certificate.identifier]
        names = []
        certificates = []
        for item in sni_certificates:
            if not isinstance(item, TlsCertificate):
                raise TypeError("SNI certificate must be TlsCertificate")
            if item.identifier in identifiers:
                raise ValueError("duplicate TLS certificate identifier")
            identifiers.append(item.identifier)
            for name in item.server_names:
                if name in names:
                    raise ValueError("duplicate SNI name across certificates")
                names.append(name)
            certificates.append(item)
        self.default_certificate = default_certificate
        self.sni_certificates = tuple(certificates)
        self.alpn = _validate_alpn(tuple(alpn))
        self.require_client_certificate = bool(require_client_certificate)
        if not isinstance(client_ca, str) or "\x00" in client_ca:
            raise ValueError("TLS client CA path must be a NUL-free string")
        if self.require_client_certificate and not client_ca:
            raise ValueError("client certificate verification requires client CA")
        self.client_ca = client_ca
        self.reject_unknown_sni = bool(reject_unknown_sni)

    def select_certificate(self, server_name: str):
        """Select exact SNI before the longest one-label wildcard."""

        if not server_name:
            return self.default_certificate
        normalized = _normalize_sni_name(server_name, False)
        wildcard_certificate = None
        wildcard_length = -1
        for certificate in self.sni_certificates:
            for pattern in certificate.server_names:
                if not pattern.startswith("*."):
                    if pattern == normalized:
                        return certificate
                    continue
                suffix = pattern[1:]
                if not normalized.endswith(suffix):
                    continue
                prefix = normalized[: -len(suffix)]
                if not prefix or "." in prefix:
                    continue
                if len(suffix) > wildcard_length:
                    wildcard_certificate = certificate
                    wildcard_length = len(suffix)
        if wildcard_certificate is not None:
            return wildcard_certificate
        if self.reject_unknown_sni:
            return None
        return self.default_certificate


class TlsProviderInfo:
    """Snapshotted provider identity attached to every result claim."""

    def __init__(self, provider) -> None:
        self.name = provider.name
        self.abi_version = provider.abi_version
        self.link_boundary = provider.link_boundary
        self.license_id = provider.license_id
        self.security_boundary = provider.security_boundary
        self.production_ready = bool(provider.production_ready)
        self.native_abi = getattr(provider, "native_abi", "")
        self.implementation_id = getattr(provider, "implementation_id", "")
        self.library_path = getattr(provider, "library_path", "")
        self.expected_library_sha256 = getattr(
            provider, "expected_library_sha256", ""
        )
        self.verified_library_sha256 = getattr(
            provider, "verified_library_sha256", ""
        )
        self.library_max_bytes = getattr(provider, "library_max_bytes", 0)


class TlsProviderRegistry:
    """Explicit provider registry with unambiguous provider ownership.

    The default registry is caller-owned and reusable by multiple generation
    managers.  Closing one manager only releases its registry lease; it never
    closes providers in an externally owned registry.  A registry constructed
    with ``owns_providers=True`` closes each provider at most once after the
    registry is closed and its final manager lease has gone away.

    ``close_on_last_manager`` is reserved for the one-provider registry created
    by :func:`production_tls_registry`.  It transfers registry ownership to its
    managers: the final manager lease closes the registry.  This distinction
    keeps an explicitly supplied, reusable registry under its caller's control.
    """

    def __init__(
        self,
        owns_providers: bool = False,
        close_on_last_manager: bool = False,
    ) -> None:
        if close_on_last_manager and not owns_providers:
            raise ValueError("manager-owned TLS registry must own its providers")
        self._lock = Lock()
        self._owns_providers = bool(owns_providers)
        self._close_on_last_manager = bool(close_on_last_manager)
        self._manager_leases = 0
        self._closed = False
        self._provider_close_started = set()
        self.providers = {}
        self.provider_info = {}

    def register(self, provider) -> None:
        name = getattr(provider, "name", "")
        if not _valid_provider_name(name):
            raise TlsProviderError("TLS provider name is invalid or duplicated")
        if getattr(provider, "abi_version", 0) != TLS_PROVIDER_ABI_VERSION:
            raise TlsProviderError("unsupported TLS provider ABI version")
        for field in ("link_boundary", "license_id", "security_boundary"):
            value = getattr(provider, field, "")
            if not isinstance(value, str) or not value:
                raise TlsProviderError("TLS provider is missing " + field)
        if not isinstance(getattr(provider, "production_ready", None), bool):
            raise TlsProviderError("TLS provider must label production readiness")
        required = (
            "create_server_context",
            "new_connection",
            "handshake",
            "set_server_context",
            "selected_alpn",
            "read",
            "write",
            "close_notify",
            "free_connection",
            "free_context",
        )
        for method in required:
            if not callable(getattr(provider, method, None)):
                raise TlsProviderError("TLS provider is missing " + method)
        if self._owns_providers and not callable(getattr(provider, "close", None)):
            raise TlsProviderError("owned TLS provider is missing close")
        self._lock.acquire()
        try:
            if self._closed:
                raise TlsProviderError("TLS provider registry is closed")
            if name in self.providers:
                raise TlsProviderError("TLS provider name is invalid or duplicated")
            self.providers[name] = provider
            self.provider_info[name] = TlsProviderInfo(provider)
        finally:
            self._lock.release()

    def _lookup_locked(self, name: str, require_production: bool):
        if self._closed:
            raise TlsProviderError("TLS provider registry is closed")
        if name not in self.providers:
            raise TlsProviderError("unknown TLS provider: " + name)
        info = self.provider_info[name]
        if require_production and not info.production_ready:
            raise TlsProviderError("TLS provider is test-only: " + name)
        return self.providers[name]

    def _activate(self, name: str, provider, require_production: bool):
        if not require_production:
            return provider
        activate = getattr(provider, "activate", None)
        if callable(activate):
            try:
                activate()
            except Exception as error:
                if isinstance(error, TlsProviderError):
                    raise
                raise TlsProviderError(
                    "TLS provider activation failed: " + name
                ) from error
            snapshot = TlsProviderInfo(provider)
            self._lock.acquire()
            try:
                if not self._closed and self.providers.get(name) is provider:
                    self.provider_info[name] = snapshot
            finally:
                self._lock.release()
        return provider

    def get(self, name: str, require_production: bool = False):
        self._lock.acquire()
        try:
            provider = self._lookup_locked(name, require_production)
        finally:
            self._lock.release()
        return self._activate(name, provider, require_production)

    def acquire_manager(self, name: str, require_production: bool = False):
        """Lease one provider for a manager, undoing the lease on failure."""

        self._lock.acquire()
        try:
            provider = self._lookup_locked(name, require_production)
            self._manager_leases += 1
        finally:
            self._lock.release()
        try:
            return self._activate(name, provider, require_production)
        except Exception:
            self.release_manager()
            raise

    def _providers_to_close_locked(self):
        if not self._owns_providers or not self._closed or self._manager_leases != 0:
            return []
        pending = []
        for name, provider in self.providers.items():
            if name not in self._provider_close_started:
                self._provider_close_started.add(name)
                pending.append(provider)
        return pending

    def _close_providers(self, providers) -> int:
        first_error = None
        closed = 0
        for provider in providers:
            close = getattr(provider, "close", None)
            if not callable(close):
                continue
            try:
                close()
                closed += 1
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise TlsProviderError(
                "TLS provider registry cleanup failed"
            ) from first_error
        return closed

    def release_manager(self) -> int:
        providers = []
        self._lock.acquire()
        try:
            if self._manager_leases <= 0:
                raise TlsProviderError("TLS registry manager lease released twice")
            self._manager_leases -= 1
            if self._close_on_last_manager and self._manager_leases == 0:
                self._closed = True
            providers = self._providers_to_close_locked()
        finally:
            self._lock.release()
        return self._close_providers(providers)

    def close(self) -> int:
        """Close registry ownership once; active manager leases defer providers."""

        providers = []
        self._lock.acquire()
        try:
            if self._closed:
                return 0
            self._closed = True
            providers = self._providers_to_close_locked()
        finally:
            self._lock.release()
        return self._close_providers(providers)

    def info(self, name: str) -> TlsProviderInfo:
        self._lock.acquire()
        try:
            if name not in self.provider_info:
                raise TlsProviderError("unknown TLS provider: " + name)
            return self.provider_info[name]
        finally:
            self._lock.release()


class _PccNativeTlsContext:
    def __init__(self, handle, certificate_id: str) -> None:
        self.handle = handle
        self.certificate_id = certificate_id
        self.released = False


class _PccNativeTlsSession:
    def __init__(self, handle) -> None:
        self.handle = handle
        self.released = False


class PccNativeTlsProvider:
    """Production adapter for the reviewed native-provider ABI.

    The shared library owns TLS wire parsing, authentication and cryptography;
    pcc owns its path, ABI validation, certificate generations, scheduling and
    all I/O readiness waits.  The library entrypoint must be
    ``pcc_tls_provider_v1_call`` and must satisfy every capability in
    :data:`PCC_TLS_REQUIRED_CAPABILITIES`.  Therefore setting this adapter's
    ``production_ready`` label never turns a state-model provider into crypto:
    listener construction calls :meth:`activate` and fails closed unless the
    native provider proves the complete contract.
    """

    name = PCC_NATIVE_TLS_PROVIDER_NAME
    abi_version = TLS_PROVIDER_ABI_VERSION
    native_abi = PCC_NATIVE_TLS_ABI_NAME
    link_boundary = (
        "explicit-shared-library:pcc_tls_provider_v1_call;"
        "no-python-ssl;no-libpython"
    )
    license_id = "provider-manifest-required"
    security_boundary = (
        "native-provider-owns-tls-records-authentication-and-crypto;"
        "pcc-owns-nonblocking-io-scheduling-and-generation-lifetime"
    )
    production_ready = True

    def __init__(
        self,
        library_path: str,
        expected_library_sha256: str,
        library_max_bytes: int = TLS_PROVIDER_DEFAULT_MAX_BYTES,
    ) -> None:
        if not isinstance(library_path, str) or not library_path:
            raise TlsProviderError("native TLS provider library path is required")
        if not library_path.startswith("/") or "\x00" in library_path:
            raise TlsProviderError(
                "native TLS provider library path must be absolute and NUL-free"
            )
        if len(library_path) > _TLS_MAX_PROVIDER_PATH:
            raise TlsProviderError("native TLS provider library path is too long")
        if not _valid_sha256_hex(expected_library_sha256):
            raise TlsProviderError(
                "native TLS provider expected SHA-256 must be 64 lowercase "
                "hexadecimal characters"
            )
        if (
            not isinstance(library_max_bytes, int)
            or isinstance(library_max_bytes, bool)
            or library_max_bytes <= 0
            or library_max_bytes > 0x7FFFFFFFFFFFFFFF
        ):
            raise TlsProviderError("native TLS provider byte limit is out of range")
        self._state_lock = Lock()
        self._activation_lock = Lock()
        self.library_path = library_path
        self.expected_library_sha256 = expected_library_sha256
        self.verified_library_sha256 = ""
        self.library_max_bytes = library_max_bytes
        self.library_handle = None
        self.entrypoint = None
        self.capabilities = 0
        self.implementation_id = "unprobed"
        self.activated = False
        self._active_contexts = 0
        self._active_sessions = 0
        self._pending_resources = 0
        self._active_calls = 0
        self._close_requested = False
        self._closed = False

    @property
    def closed(self) -> bool:
        self._state_lock.acquire()
        try:
            return self._closed
        finally:
            self._state_lock.release()

    @property
    def active_contexts(self) -> int:
        self._state_lock.acquire()
        try:
            return self._active_contexts
        finally:
            self._state_lock.release()

    @property
    def active_sessions(self) -> int:
        self._state_lock.acquire()
        try:
            return self._active_sessions
        finally:
            self._state_lock.release()

    def _take_library_to_close_locked(self):
        if (
            not self._close_requested
            or self._closed
            or self._active_contexts != 0
            or self._active_sessions != 0
            or self._pending_resources != 0
            or self._active_calls != 0
        ):
            return None
        library = self.library_handle
        self.library_handle = None
        self.entrypoint = None
        self.activated = False
        self._closed = True
        return library

    def _close_library(self, library) -> None:
        if library is not None:
            dynamic_library_close(library)

    def _finish_call(self) -> None:
        library = None
        self._state_lock.acquire()
        try:
            if self._active_calls <= 0:
                raise TlsProviderError("native TLS provider call accounting underflow")
            self._active_calls -= 1
            library = self._take_library_to_close_locked()
        finally:
            self._state_lock.release()
        self._close_library(library)

    def _begin_call(self):
        self._state_lock.acquire()
        try:
            if (
                self._closed
                or self.entrypoint is None
                or ptr_to_int(self.entrypoint) == 0
            ):
                raise TlsProviderError("native TLS provider is not activated")
            self._active_calls += 1
            return self.entrypoint
        finally:
            self._state_lock.release()

    def _reserve_resource(self) -> None:
        self._state_lock.acquire()
        try:
            if self._close_requested or self._closed:
                raise TlsProviderError("native TLS provider is closing")
            self._pending_resources += 1
        finally:
            self._state_lock.release()

    def _publish_resource(self, resource_kind: str) -> None:
        self._state_lock.acquire()
        try:
            if self._pending_resources <= 0:
                raise TlsProviderError(
                    "native TLS provider resource accounting underflow"
                )
            self._pending_resources -= 1
            if resource_kind == "context":
                self._active_contexts += 1
            elif resource_kind == "session":
                self._active_sessions += 1
            else:
                raise TlsProviderError("unknown native TLS provider resource kind")
        finally:
            self._state_lock.release()

    def _cancel_resource(self) -> None:
        library = None
        self._state_lock.acquire()
        try:
            if self._pending_resources <= 0:
                raise TlsProviderError(
                    "native TLS provider resource accounting underflow"
                )
            self._pending_resources -= 1
            library = self._take_library_to_close_locked()
        finally:
            self._state_lock.release()
        self._close_library(library)

    def _release_resource(self, resource_kind: str) -> None:
        library = None
        self._state_lock.acquire()
        try:
            if resource_kind == "context":
                if self._active_contexts <= 0:
                    raise TlsProviderError(
                        "native TLS provider context accounting underflow"
                    )
                self._active_contexts -= 1
            elif resource_kind == "session":
                if self._active_sessions <= 0:
                    raise TlsProviderError(
                        "native TLS provider session accounting underflow"
                    )
                self._active_sessions -= 1
            else:
                raise TlsProviderError("unknown native TLS provider resource kind")
            library = self._take_library_to_close_locked()
        finally:
            self._state_lock.release()
        self._close_library(library)

    def _request(self, operation: int):
        request = stack_alloc(_PCC_TLS_REQUEST_BYTES)
        offset = 0
        while offset < _PCC_TLS_REQUEST_BYTES:
            store_i64(request, offset, 0)
            offset += 8
        store_i64(request, _PCC_TLS_REQ_SIZE, _PCC_TLS_REQUEST_BYTES)
        store_i64(request, _PCC_TLS_REQ_ABI, PCC_NATIVE_TLS_ABI_VERSION)
        store_i64(request, _PCC_TLS_REQ_OPERATION, operation)
        return request

    def _invoke(self, operation: int, request) -> int:
        entrypoint = self._begin_call()
        try:
            return self._invoke_entrypoint(entrypoint, operation, request)
        finally:
            self._finish_call()

    def _invoke_entrypoint(self, entrypoint, operation: int, request) -> int:
        status = call_i64_i64_ptr(entrypoint, operation, request)
        mirrored = load_i64(request, _PCC_TLS_REQ_STATUS)
        if mirrored != status:
            raise TlsProviderError(
                "native TLS provider returned inconsistent operation status"
            )
        if status not in (
            TLS_OK,
            TLS_WANT_READ,
            TLS_WANT_WRITE,
            TLS_CLOSED,
            TLS_SELECT_SNI,
            TLS_ERROR,
        ):
            raise TlsProviderError("native TLS provider returned unknown status")
        return status

    def _result(self, status: int, request, count: int = 0, server_name=""):
        error_code = load_i64(request, _PCC_TLS_REQ_ERROR)
        if status == TLS_ERROR:
            if error_code <= TLS_ERR_NONE or error_code > TLS_ERR_INTERNAL:
                error_code = TLS_ERR_PROVIDER_CONTRACT
        elif error_code != TLS_ERR_NONE:
            return TlsResult(
                TLS_ERROR,
                error_code=TLS_ERR_PROVIDER_CONTRACT,
                detail="native provider mixed success status and error",
            )
        return TlsResult(
            status,
            count=count,
            error_code=error_code,
            detail=(
                "native provider operation failed"
                if status == TLS_ERROR
                else ""
            ),
            server_name=server_name,
        )

    def _read_text(self, request, storage, capacity: int) -> str:
        length = load_i64(request, _PCC_TLS_REQ_OUTPUT0_LEN)
        if length < 0 or length >= capacity:
            raise TlsProviderError("native TLS provider returned invalid text length")
        if length == 0:
            return ""
        value = _py_str_new(storage, length)
        if not isinstance(value, str):
            raise TlsProviderError("native TLS provider text conversion failed")
        return value

    def activate(self) -> None:
        self._activation_lock.acquire()
        try:
            self._state_lock.acquire()
            try:
                if self.activated and not self._closed:
                    return
                if self._close_requested or self._closed:
                    raise TlsProviderError("native TLS provider is closed")
            finally:
                self._state_lock.release()

            # Snapshot the validated artifact identity once.  The path used by
            # the loader must be the same Python string value that was hashed;
            # re-reading a publicly visible field after hashing would add a
            # separate mutable-config race on top of the documented filesystem
            # hash-then-open boundary.
            library_path = self.library_path
            expected_library_sha256 = self.expected_library_sha256
            library_max_bytes = self.library_max_bytes
            try:
                actual_library_sha256 = os._pcc_sha256_file_hex_bounded(
                    library_path,
                    library_max_bytes,
                )
            except Exception as error:
                raise TlsProviderError(
                    "native TLS provider library hashing failed"
                ) from error
            if not _valid_sha256_hex(actual_library_sha256):
                raise TlsProviderError(
                    "native TLS provider library could not be hashed within "
                    "the configured byte limit"
                )
            if actual_library_sha256 != expected_library_sha256:
                raise TlsProviderError(
                    "native TLS provider library SHA-256 mismatch"
                )

            library = dynamic_library_open(_py_str_utf8(library_path))
            if library is None or ptr_to_int(library) == 0:
                raise TlsProviderError(
                    "native TLS provider library could not be opened"
                )
            try:
                entrypoint = dynamic_library_symbol(
                    library, cstr("pcc_tls_provider_v1_call")
                )
                if entrypoint is None or ptr_to_int(entrypoint) == 0:
                    raise TlsProviderError("native TLS provider entrypoint is missing")

                request = self._request(PCC_TLS_OP_PROBE)
                identity_storage = stack_alloc(65)
                store_ptr(request, _PCC_TLS_REQ_OUTPUT0, identity_storage)
                store_i64(request, _PCC_TLS_REQ_OUTPUT0_CAP, 64)
                status = self._invoke_entrypoint(
                    entrypoint, PCC_TLS_OP_PROBE, request
                )
                if status != TLS_OK:
                    raise TlsProviderError("native TLS provider ABI probe failed")
                capabilities = load_i64(request, _PCC_TLS_REQ_FLAGS)
                if (
                    capabilities & PCC_TLS_REQUIRED_CAPABILITIES
                ) != PCC_TLS_REQUIRED_CAPABILITIES:
                    raise TlsProviderError(
                        "native TLS provider is missing required capabilities"
                    )
                identity = self._read_text(request, identity_storage, 65)
                if not identity or len(identity) > 64:
                    raise TlsProviderError("native TLS provider identity is invalid")

                published = False
                self._state_lock.acquire()
                try:
                    if not self._close_requested and not self._closed:
                        self.library_handle = library
                        self.entrypoint = entrypoint
                        self.capabilities = capabilities
                        self.implementation_id = identity
                        self.verified_library_sha256 = actual_library_sha256
                        self.activated = True
                        published = True
                finally:
                    self._state_lock.release()
                if not published:
                    raise TlsProviderError(
                        "native TLS provider closed during activation"
                    )
            except Exception:
                dynamic_library_close(library)
                raise
        finally:
            self._activation_lock.release()

    def _alpn_wire(self, protocols) -> str:
        # Text framing is part of the provider ABI, not an OpenSSL wire value.
        # A decimal byte length prevents delimiter ambiguity.
        output = ""
        for protocol in protocols:
            output += str(len(protocol)) + ":" + protocol
        return output

    def create_server_context(self, config, certificate):
        self.activate()
        if (
            config.require_client_certificate
            and self.capabilities & PCC_TLS_CAP_CLIENT_CERTIFICATE == 0
        ):
            raise TlsProviderError(
                "native TLS provider cannot verify client certificates"
            )
        if not isinstance(certificate.certificate, str) or not isinstance(
            certificate.private_key, str
        ):
            raise TlsProviderError(
                "native TLS provider requires certificate and key path strings"
            )
        if "\x00" in certificate.certificate or "\x00" in certificate.private_key:
            raise TlsProviderError("native TLS certificate paths contain NUL")
        if not certificate.certificate.startswith("/") or not (
            certificate.private_key.startswith("/")
        ):
            raise TlsProviderError(
                "native TLS certificate and key paths must be absolute"
            )
        if config.client_ca and not config.client_ca.startswith("/"):
            raise TlsProviderError("native TLS client CA path must be absolute")
        certificate_length = _py_str_byte_len(certificate.certificate)
        private_key_length = _py_str_byte_len(certificate.private_key)
        client_ca_length = _py_str_byte_len(config.client_ca)
        if (
            certificate_length > _TLS_MAX_PROVIDER_PATH
            or private_key_length > _TLS_MAX_PROVIDER_PATH
            or client_ca_length > _TLS_MAX_PROVIDER_PATH
        ):
            raise TlsProviderError("native TLS certificate path is too long")
        alpn = self._alpn_wire(config.alpn)
        request = self._request(PCC_TLS_OP_CONTEXT_CREATE)
        certificate_path = _py_str_utf8(certificate.certificate)
        private_key_path = _py_str_utf8(certificate.private_key)
        alpn_value = _py_str_utf8(alpn)
        store_ptr(request, _PCC_TLS_REQ_INPUT0, certificate_path)
        store_i64(
            request,
            _PCC_TLS_REQ_INPUT0_LEN,
            certificate_length,
        )
        store_ptr(request, _PCC_TLS_REQ_INPUT1, private_key_path)
        store_i64(
            request,
            _PCC_TLS_REQ_INPUT1_LEN,
            private_key_length,
        )
        store_ptr(request, _PCC_TLS_REQ_INPUT2, alpn_value)
        store_i64(request, _PCC_TLS_REQ_INPUT2_LEN, _py_str_byte_len(alpn))
        client_ca_value = _py_str_utf8(config.client_ca)
        store_ptr(request, _PCC_TLS_REQ_INPUT3, client_ca_value)
        store_i64(
            request, _PCC_TLS_REQ_INPUT3_LEN, client_ca_length
        )
        store_i64(
            request,
            _PCC_TLS_REQ_FLAGS,
            1 if config.require_client_certificate else 0,
        )
        self._reserve_resource()
        try:
            status = self._invoke(PCC_TLS_OP_CONTEXT_CREATE, request)
            if status != TLS_OK:
                raise TlsProviderError(
                    "native TLS provider rejected certificate generation"
                )
            handle = load_ptr(request, _PCC_TLS_REQ_PRIMARY)
            if handle is None or ptr_to_int(handle) == 0:
                raise TlsProviderError("native TLS provider returned a null context")
        except Exception:
            self._cancel_resource()
            raise
        self._publish_resource("context")
        return _PccNativeTlsContext(handle, certificate.identifier)

    def new_connection(self, context, fd):
        self._state_lock.acquire()
        try:
            if not isinstance(context, _PccNativeTlsContext) or context.released:
                raise TlsProviderError("native TLS context is released")
            if self._close_requested or self._closed:
                raise TlsProviderError("native TLS provider is closing")
            context_handle = context.handle
            self._pending_resources += 1
        finally:
            self._state_lock.release()
        try:
            request = self._request(PCC_TLS_OP_CONNECTION_CREATE)
            store_ptr(request, _PCC_TLS_REQ_PRIMARY, context_handle)
            store_i64(request, _PCC_TLS_REQ_SECONDARY, fd)
            status = self._invoke(PCC_TLS_OP_CONNECTION_CREATE, request)
            if status != TLS_OK:
                raise TlsProviderError("native TLS connection creation failed")
            handle = load_ptr(request, _PCC_TLS_REQ_PRIMARY)
            if handle is None or ptr_to_int(handle) == 0:
                raise TlsProviderError(
                    "native TLS provider returned a null connection"
                )
        except Exception:
            self._cancel_resource()
            raise
        self._publish_resource("session")
        return _PccNativeTlsSession(handle)

    def handshake(self, session):
        request = self._session_request(PCC_TLS_OP_HANDSHAKE, session)
        name_storage = stack_alloc(_TLS_MAX_SNI_NAME + 1)
        store_ptr(request, _PCC_TLS_REQ_OUTPUT0, name_storage)
        store_i64(request, _PCC_TLS_REQ_OUTPUT0_CAP, _TLS_MAX_SNI_NAME)
        status = self._invoke(PCC_TLS_OP_HANDSHAKE, request)
        server_name = ""
        if status == TLS_SELECT_SNI:
            server_name = self._read_text(
                request, name_storage, _TLS_MAX_SNI_NAME + 1
            )
        return self._result(status, request, server_name=server_name)

    def _session_request(self, operation: int, session):
        if not isinstance(session, _PccNativeTlsSession) or session.released:
            raise TlsProviderError("native TLS connection is released")
        request = self._request(operation)
        store_ptr(request, _PCC_TLS_REQ_PRIMARY, session.handle)
        return request

    def set_server_context(self, session, context):
        if not isinstance(context, _PccNativeTlsContext) or context.released:
            return _tls_error(TLS_ERR_PROVIDER, "native TLS context is released")
        request = self._session_request(PCC_TLS_OP_SET_CONTEXT, session)
        store_ptr(request, _PCC_TLS_REQ_SECONDARY, context.handle)
        status = self._invoke(PCC_TLS_OP_SET_CONTEXT, request)
        return self._result(status, request)

    def selected_alpn(self, session):
        request = self._session_request(PCC_TLS_OP_SELECTED_ALPN, session)
        storage = stack_alloc(256)
        store_ptr(request, _PCC_TLS_REQ_OUTPUT0, storage)
        store_i64(request, _PCC_TLS_REQ_OUTPUT0_CAP, 255)
        status = self._invoke(PCC_TLS_OP_SELECTED_ALPN, request)
        if status != TLS_OK:
            raise TlsProviderError("native TLS provider ALPN query failed")
        return self._read_text(request, storage, 256)

    def read(self, session, output, limit):
        if not isinstance(output, bytearray):
            return _tls_error(TLS_ERR_PROVIDER_CONTRACT, "TLS read needs bytearray")
        request = self._session_request(PCC_TLS_OP_READ, session)
        store_ptr(
            request,
            _PCC_TLS_REQ_OUTPUT0,
            ptr_add(output, PYBYTEARRAYOBJECT_DATA_OFFSET),
        )
        store_i64(request, _PCC_TLS_REQ_OUTPUT0_CAP, limit)
        status = self._invoke(PCC_TLS_OP_READ, request)
        count = load_i64(request, _PCC_TLS_REQ_OUTPUT0_LEN)
        return self._result(status, request, count=count if status == TLS_OK else 0)

    def write(self, session, data, length):
        if not isinstance(data, bytes):
            return _tls_error(TLS_ERR_PROVIDER_CONTRACT, "TLS write needs bytes")
        request = self._session_request(PCC_TLS_OP_WRITE, session)
        store_ptr(
            request,
            _PCC_TLS_REQ_INPUT0,
            ptr_add(data, PYBYTESOBJECT_DATA_OFFSET),
        )
        store_i64(request, _PCC_TLS_REQ_INPUT0_LEN, length)
        status = self._invoke(PCC_TLS_OP_WRITE, request)
        count = load_i64(request, _PCC_TLS_REQ_OUTPUT0_LEN)
        return self._result(status, request, count=count if status == TLS_OK else 0)

    def close_notify(self, session):
        request = self._session_request(PCC_TLS_OP_CLOSE_NOTIFY, session)
        status = self._invoke(PCC_TLS_OP_CLOSE_NOTIFY, request)
        return self._result(status, request)

    def free_connection(self, session):
        self._state_lock.acquire()
        try:
            if not isinstance(session, _PccNativeTlsSession) or session.released:
                return
            handle = session.handle
            session.released = True
            session.handle = None
        finally:
            self._state_lock.release()
        try:
            request = self._request(PCC_TLS_OP_CONNECTION_FREE)
            store_ptr(request, _PCC_TLS_REQ_PRIMARY, handle)
            status = self._invoke(PCC_TLS_OP_CONNECTION_FREE, request)
            if status not in (TLS_OK, TLS_CLOSED):
                raise TlsProviderError("native TLS connection cleanup failed")
        finally:
            self._release_resource("session")

    def free_context(self, context):
        self._state_lock.acquire()
        try:
            if not isinstance(context, _PccNativeTlsContext) or context.released:
                return
            handle = context.handle
            context.released = True
            context.handle = None
        finally:
            self._state_lock.release()
        try:
            request = self._request(PCC_TLS_OP_CONTEXT_FREE)
            store_ptr(request, _PCC_TLS_REQ_PRIMARY, handle)
            status = self._invoke(PCC_TLS_OP_CONTEXT_FREE, request)
            if status not in (TLS_OK, TLS_CLOSED):
                raise TlsProviderError("native TLS context cleanup failed")
        finally:
            self._release_resource("context")

    def close(self) -> int:
        """Request one close and unload only after all native owners drain."""

        library = None
        closed_now = False
        self._state_lock.acquire()
        try:
            if self._closed or self._close_requested:
                return 0
            self._close_requested = True
            library = self._take_library_to_close_locked()
            closed_now = self._closed
        finally:
            self._state_lock.release()
        self._close_library(library)
        return 1 if closed_now else 0


def production_tls_registry(
    library_path: str,
    expected_library_sha256: str,
    library_max_bytes: int = TLS_PROVIDER_DEFAULT_MAX_BYTES,
) -> TlsProviderRegistry:
    """Create the fail-closed registry used by a native HTTPS listener."""

    registry = TlsProviderRegistry(
        owns_providers=True,
        close_on_last_manager=True,
    )
    registry.register(
        PccNativeTlsProvider(
            library_path,
            expected_library_sha256,
            library_max_bytes,
        )
    )
    return registry


class TlsGeneration:
    """Reference-counted certificate-context generation.

    The initial reference belongs to the publishing lifecycle owner.  Channels
    retain one reference.  Retirement drops only the owner reference, allowing
    old connections to drain before all contexts are freed exactly once.
    """

    def __init__(self, generation_id: int, provider, config: TlsConfig) -> None:
        if generation_id <= 0:
            raise ValueError("TLS generation id must be positive")
        if not isinstance(config, TlsConfig):
            raise TypeError("TLS generation config must be TlsConfig")
        self._lock = Lock()
        self.generation_id = generation_id
        self.provider = provider
        self.provider_name = provider.name
        self.provider_info = TlsProviderInfo(provider)
        self.config = config
        self._references = 1
        self._retired = False
        self._destroyed = False
        self._contexts = []
        self._context_by_identifier = {}
        self._context = None
        certificates = (config.default_certificate,) + config.sni_certificates
        try:
            for certificate in certificates:
                context = provider.create_server_context(config, certificate)
                if context is None:
                    raise TlsProviderError(
                        "TLS provider failed to create context "
                        + certificate.identifier
                    )
                self._contexts.append(context)
                self._context_by_identifier[certificate.identifier] = context
        except Exception as error:
            self._lock.acquire()
            try:
                self._references = 0
                self._retired = True
                contexts = self._take_contexts_locked()
            finally:
                self._lock.release()
            try:
                self._free_contexts(contexts)
            except Exception as cleanup_error:
                raise TlsProviderError(
                    "TLS provider context creation cleanup failed"
                ) from cleanup_error
            if isinstance(error, TlsProviderError):
                raise
            raise TlsProviderError("TLS provider context creation failed") from error
        self._context = self._context_by_identifier[
            config.default_certificate.identifier
        ]

    @property
    def references(self) -> int:
        self._lock.acquire()
        try:
            return self._references
        finally:
            self._lock.release()

    @property
    def retired(self) -> bool:
        self._lock.acquire()
        try:
            return self._retired
        finally:
            self._lock.release()

    @property
    def destroyed(self) -> bool:
        self._lock.acquire()
        try:
            return self._destroyed
        finally:
            self._lock.release()

    @property
    def context(self):
        self._lock.acquire()
        try:
            if self._destroyed or self._context is None:
                raise TlsProviderError("TLS generation is released")
            return self._context
        finally:
            self._lock.release()

    def retain(self):
        self._lock.acquire()
        try:
            if self._references <= 0 or self._destroyed:
                raise TlsProviderError("TLS generation is released")
            self._references += 1
        finally:
            self._lock.release()
        return self

    def _take_contexts_locked(self):
        if self._destroyed:
            return None
        contexts = self._contexts
        self._contexts = []
        self._context_by_identifier = {}
        self._context = None
        self._destroyed = True
        return contexts

    def _free_contexts(self, contexts) -> None:
        if contexts is None:
            return
        first_error = None
        index = len(contexts) - 1
        while index >= 0:
            context = contexts[index]
            try:
                self.provider.free_context(context)
            except Exception as error:
                if first_error is None:
                    first_error = error
            index -= 1
        if first_error is not None:
            raise TlsProviderError(
                "TLS provider context cleanup failed"
            ) from first_error

    def release(self) -> int:
        contexts = None
        self._lock.acquire()
        try:
            if self._references <= 0 or self._destroyed:
                raise TlsProviderError("TLS generation released more than once")
            self._references -= 1
            if self._references == 0:
                contexts = self._take_contexts_locked()
        finally:
            self._lock.release()
        self._free_contexts(contexts)
        return 1 if contexts is not None else 0

    def retire(self) -> int:
        contexts = None
        self._lock.acquire()
        try:
            if self._retired:
                return 0
            if self._references <= 0 or self._destroyed:
                raise TlsProviderError("TLS generation released more than once")
            self._retired = True
            self._references -= 1
            if self._references == 0:
                contexts = self._take_contexts_locked()
        finally:
            self._lock.release()
        self._free_contexts(contexts)
        return 1 if contexts is not None else 0

    def select_context(self, server_name: str):
        certificate = self.config.select_certificate(server_name)
        if certificate is None:
            return None, ""
        self._lock.acquire()
        try:
            if self._destroyed:
                raise TlsProviderError("TLS generation is released")
            context = self._context_by_identifier.get(certificate.identifier)
            if context is None:
                raise TlsProviderError("TLS generation context is missing")
            return context, certificate.identifier
        finally:
            self._lock.release()


class TlsGenerationManager:
    """Publish/retain/release semantics for certificate reload.

    Publication is serialized independently of connection-owned generations.
    The manager leases (but does not necessarily own) its provider registry.
    Closing a manager never closes an externally owned registry; the registry
    returned by :func:`production_tls_registry` explicitly transfers that
    ownership and closes itself after its final manager lease.
    """

    def __init__(
        self,
        registry: TlsProviderRegistry,
        provider_name: str,
        config: TlsConfig,
        require_production: bool = True,
    ) -> None:
        self._lock = Lock()
        self._reload_lock = Lock()
        self.registry = registry
        self.provider_name = provider_name
        self.provider = registry.acquire_manager(provider_name, require_production)
        self._registry_lease_active = True
        try:
            self.provider_info = registry.info(provider_name)
            self.next_generation_id = 2
            self._active = TlsGeneration(1, self.provider, config)
            self._closed = False
        except Exception:
            self._registry_lease_active = False
            registry.release_manager()
            raise

    @property
    def active(self):
        self._lock.acquire()
        try:
            return self._active
        finally:
            self._lock.release()

    @property
    def closed(self) -> bool:
        self._lock.acquire()
        try:
            return self._closed
        finally:
            self._lock.release()

    def new_channel(self, fd: int, generation=None):
        if fd < 0:
            raise ValueError("TLS channel requires a nonnegative socket fd")
        self._lock.acquire()
        try:
            if self._closed or self._active is None:
                raise TlsProviderError("TLS generation manager is closed")
            selected = self._active if generation is None else generation
            if selected.provider is not self.provider:
                raise TlsProviderError("TLS generation belongs to another provider")
            selected.retain()
        finally:
            self._lock.release()
        return TlsChannel(selected, fd, generation_retained=True)

    def reload(self, config: TlsConfig) -> TlsGeneration:
        replacement = None
        previous = None
        publish = False
        self._reload_lock.acquire()
        try:
            self._lock.acquire()
            try:
                if self._closed or self._active is None:
                    raise TlsProviderError("TLS generation manager is closed")
                generation_id = self.next_generation_id
                self.next_generation_id += 1
            finally:
                self._lock.release()

            replacement = TlsGeneration(generation_id, self.provider, config)
            self._lock.acquire()
            try:
                if self._closed or self._active is None:
                    publish = False
                    previous = None
                else:
                    publish = True
                    previous = self._active
                    self._active = replacement
            finally:
                self._lock.release()
        finally:
            self._reload_lock.release()
        # Context destruction invokes provider code and must not run under
        # either manager lock.  Publication is already complete or rejected.
        if not publish:
            replacement.retire()
            raise TlsProviderError("TLS generation manager closed during reload")
        previous.retire()
        return replacement

    def close(self) -> int:
        active = None
        registry_lease_active = False
        already_closed = False
        self._reload_lock.acquire()
        try:
            self._lock.acquire()
            try:
                if self._closed:
                    already_closed = True
                else:
                    self._closed = True
                    active = self._active
                    self._active = None
                    registry_lease_active = self._registry_lease_active
                    self._registry_lease_active = False
            finally:
                self._lock.release()
        finally:
            self._reload_lock.release()
        if already_closed:
            return 0

        # Generation/provider/registry cleanup can invoke arbitrary native
        # owner callbacks.  The manager state is terminal before any callback.
        released = 0
        first_error = None
        if active is not None:
            try:
                released = active.retire()
            except Exception as error:
                first_error = error
        if registry_lease_active:
            try:
                self.registry.release_manager()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise TlsProviderError(
                "TLS generation manager cleanup failed"
            ) from first_error
        return released


class TlsChannel:
    """One nonblocking TLS session with absolute-deadline operations."""

    def __init__(
        self,
        generation: TlsGeneration,
        fd: int,
        generation_retained: bool = False,
    ) -> None:
        if fd < 0:
            raise ValueError("TLS channel requires a nonnegative socket fd")
        self._close_lock = Lock()
        if generation_retained:
            self.generation = generation
        else:
            self.generation = generation.retain()
        self.provider = generation.provider
        self.provider_name = generation.provider_name
        self.provider_info = generation.provider_info
        self.session = None
        try:
            self.session = self.provider.new_connection(generation.context, fd)
        except Exception as error:
            self.generation.release()
            self.generation = None
            raise TlsProviderError("TLS provider connection creation failed") from error
        if self.session is None:
            self.generation.release()
            self.generation = None
            raise TlsProviderError("TLS provider failed to create connection")
        self.fd = fd
        self.handshake_complete = False
        self.sni_selected = False
        self.server_name = ""
        self.certificate_id = generation.config.default_certificate.identifier
        self.alpn = ""
        self.peer_closed = False
        self.close_notify_started = False
        self.close_notify_complete = False
        self.failed = False
        self.released = False
        self.closed = False
        self.last_error = "none"
        self.last_result = TlsResult(TLS_OK)

    def _remember(self, result: TlsResult) -> TlsResult:
        self.last_result = result
        self.last_error = result.error_name
        if result.status == TLS_ERROR:
            self.failed = True
        return result

    def _provider_failure(self, detail: str) -> TlsResult:
        return self._remember(_tls_error(TLS_ERR_PROVIDER, detail))

    def _contract_failure(self, detail: str) -> TlsResult:
        return self._remember(_tls_error(TLS_ERR_PROVIDER_CONTRACT, detail))

    def _deadline(self, now_ms: int, deadline_ms: int):
        if now_ms < 0 or deadline_ms <= 0:
            return self._remember(
                _tls_error(TLS_ERR_CONFIGURATION, "invalid absolute deadline")
            )
        if now_ms >= deadline_ms:
            return self._remember(_tls_error(TLS_ERR_DEADLINE, "deadline expired"))
        return None

    def _call_result(self, method_name: str, *arguments):
        try:
            result = getattr(self.provider, method_name)(*arguments)
        except Exception as error:
            return self._provider_failure(method_name + ": " + str(error))
        if not isinstance(result, TlsResult):
            return self._contract_failure(method_name + " returned a non-TlsResult")
        return result

    def _validate_result(self, operation: str, result: TlsResult, limit: int = 0):
        if result.status not in (
            TLS_OK,
            TLS_WANT_READ,
            TLS_WANT_WRITE,
            TLS_CLOSED,
            TLS_SELECT_SNI,
            TLS_ERROR,
        ):
            return self._contract_failure(operation + " returned an unknown status")
        if result.status == TLS_SELECT_SNI and operation != "handshake":
            return self._contract_failure(operation + " requested SNI selection")
        if result.status == TLS_SELECT_SNI and not result.server_name:
            return self._contract_failure("handshake requested empty SNI")
        if result.status != TLS_SELECT_SNI and result.server_name:
            return self._contract_failure(operation + " returned unexpected SNI")
        if result.status == TLS_ERROR:
            if result.error_code <= TLS_ERR_NONE or result.error_code > TLS_ERR_INTERNAL:
                return self._contract_failure(operation + " returned invalid error code")
            if result.count != 0:
                return self._contract_failure(operation + " error returned byte count")
            return self._remember(result)
        if result.error_code != TLS_ERR_NONE:
            return self._contract_failure(operation + " mixed status and error")
        if operation in ("read", "write") and result.status == TLS_OK:
            if result.count <= 0 or result.count > limit:
                return self._contract_failure(operation + " returned invalid byte count")
        elif result.count != 0:
            return self._contract_failure(operation + " returned unexpected byte count")
        return self._remember(result)

    def _preflight(self, now_ms: int, deadline_ms: int):
        if self.released or self.session is None:
            return self._remember(TlsResult(TLS_CLOSED))
        if self.failed:
            return self.last_result
        if self.closed:
            return self._remember(TlsResult(TLS_CLOSED))
        return self._deadline(now_ms, deadline_ms)

    def handshake(self, now_ms: int, deadline_ms: int) -> TlsResult:
        preflight = self._preflight(now_ms, deadline_ms)
        if preflight is not None:
            return preflight
        if self.handshake_complete:
            return self._remember(TlsResult(TLS_OK))
        result = self._call_result("handshake", self.session)
        if result.status == TLS_ERROR and self.failed:
            return result
        result = self._validate_result("handshake", result)
        if result.status == TLS_SELECT_SNI:
            if self.sni_selected:
                return self._contract_failure("provider requested SNI more than once")
            try:
                normalized = _normalize_sni_name(result.server_name, False)
            except ValueError as error:
                return self._remember(_tls_error(TLS_ERR_PROTOCOL, str(error)))
            context, certificate_id = self.generation.select_context(normalized)
            if context is None:
                return self._remember(
                    _tls_error(TLS_ERR_UNRECOGNIZED_NAME, "SNI name is not configured")
                )
            selected = self._call_result(
                "set_server_context", self.session, context
            )
            if selected.status == TLS_ERROR and self.failed:
                return selected
            selected = self._validate_result("set_server_context", selected)
            if selected.status != TLS_OK:
                return self._contract_failure(
                    "set_server_context must complete synchronously"
                )
            self.sni_selected = True
            self.server_name = normalized
            self.certificate_id = certificate_id
            return self._remember(
                TlsResult(TLS_SELECT_SNI, server_name=normalized)
            )
        if result.status == TLS_OK:
            try:
                selected_alpn = self.provider.selected_alpn(self.session)
            except Exception as error:
                return self._provider_failure("selected_alpn: " + str(error))
            if not isinstance(selected_alpn, str):
                return self._contract_failure("selected_alpn returned a non-string")
            if selected_alpn and selected_alpn not in self.generation.config.alpn:
                return self._remember(
                    _tls_error(TLS_ERR_ALPN, "provider selected unconfigured ALPN")
                )
            self.alpn = selected_alpn
            self.handshake_complete = True
        elif result.status == TLS_CLOSED:
            self.peer_closed = True
        return result

    def read(self, output, limit: int, now_ms: int, deadline_ms: int) -> TlsResult:
        preflight = self._preflight(now_ms, deadline_ms)
        if preflight is not None:
            return preflight
        if not self.handshake_complete:
            return self._remember(
                _tls_error(TLS_ERR_CONFIGURATION, "TLS read before handshake")
            )
        if limit <= 0 or limit > len(output):
            return self._remember(
                _tls_error(TLS_ERR_CONFIGURATION, "invalid TLS read limit")
            )
        result = self._call_result("read", self.session, output, limit)
        if result.status == TLS_ERROR and self.failed:
            return result
        result = self._validate_result("read", result, limit)
        if result.status == TLS_CLOSED:
            self.peer_closed = True
        return result

    def write(self, data, length: int, now_ms: int, deadline_ms: int) -> TlsResult:
        preflight = self._preflight(now_ms, deadline_ms)
        if preflight is not None:
            return preflight
        if not self.handshake_complete:
            return self._remember(
                _tls_error(TLS_ERR_CONFIGURATION, "TLS write before handshake")
            )
        if self.close_notify_started:
            return self._remember(
                _tls_error(TLS_ERR_CONFIGURATION, "TLS write after close-notify")
            )
        if length <= 0 or length > len(data):
            return self._remember(
                _tls_error(TLS_ERR_CONFIGURATION, "invalid TLS write length")
            )
        result = self._call_result("write", self.session, data, length)
        if result.status == TLS_ERROR and self.failed:
            return result
        return self._validate_result("write", result, length)

    def close_notify(self, now_ms: int, deadline_ms: int) -> TlsResult:
        preflight = self._preflight(now_ms, deadline_ms)
        if preflight is not None:
            return preflight
        if not self.handshake_complete:
            return self._remember(
                _tls_error(TLS_ERR_CONFIGURATION, "close-notify before handshake")
            )
        if self.close_notify_complete:
            return self._remember(TlsResult(TLS_CLOSED))
        self.close_notify_started = True
        result = self._call_result("close_notify", self.session)
        if result.status == TLS_ERROR and self.failed:
            return result
        result = self._validate_result("close_notify", result)
        if result.status == TLS_OK:
            return self._contract_failure(
                "close_notify must return WANT_READ, WANT_WRITE, CLOSED or ERROR"
            )
        if result.status == TLS_CLOSED:
            self.close_notify_complete = True
            self.closed = True
        return result

    def cancel(self) -> TlsResult:
        if self.released:
            return self._remember(TlsResult(TLS_CLOSED))
        return self._remember(_tls_error(TLS_ERR_CANCELLED, "TLS operation cancelled"))

    def close(self) -> TlsResult:
        """Force-release session and generation once; never performs blocking I/O."""

        self._close_lock.acquire()
        try:
            if self.released:
                return TlsResult(TLS_CLOSED)
            self.released = True
            self.closed = True
            session = self.session
            generation = self.generation
            self.session = None
            self.generation = None
        finally:
            self._close_lock.release()
        first_error = None
        if session is not None:
            try:
                self.provider.free_connection(session)
            except Exception as error:
                first_error = error
        if generation is not None:
            try:
                generation.release()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            return self._remember(
                _tls_error(TLS_ERR_PROVIDER, "TLS cleanup failed: " + str(first_error))
            )
        return self._remember(TlsResult(TLS_CLOSED))
