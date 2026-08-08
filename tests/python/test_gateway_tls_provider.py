"""Finite TLS-provider ABI, ownership and claim-hygiene source tests.

The scripted provider below performs no cryptography and is permanently marked
``production_ready = False``.  Passing these host/model tests or the pcc1 ABI
fixture must never be reported as an HTTPS result.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Barrier, Thread

import pytest

import pcc.gateway.tls as tls_module

from pcc.gateway.tls import (
    TLS_CLOSED,
    TLS_ERROR,
    TLS_ERR_ALPN,
    TLS_ERR_CANCELLED,
    TLS_ERR_DEADLINE,
    TLS_ERR_PROTOCOL,
    TLS_ERR_PROVIDER,
    TLS_ERR_PROVIDER_CONTRACT,
    TLS_ERR_UNRECOGNIZED_NAME,
    TLS_INTEREST_READ,
    TLS_INTEREST_WRITE,
    TLS_OK,
    TLS_PROVIDER_ABI_VERSION,
    TLS_SELECT_SNI,
    TLS_WANT_READ,
    TLS_WANT_WRITE,
    PCC_NATIVE_TLS_PROVIDER_NAME,
    PCC_TLS_REQUIRED_CAPABILITIES,
    PccNativeTlsProvider,
    TlsCertificate,
    TlsChannel,
    TlsConfig,
    TlsGeneration,
    TlsGenerationManager,
    TlsProviderError,
    TlsProviderRegistry,
    TlsResult,
    tls_error_name,
)

from pcc1_gate import find_current_pcc1


REPO = Path(__file__).resolve().parents[2]
PCC1_TLS_ABI_SOURCE = (
    REPO / "tests" / "fixtures" / "gateway" / "current_pcc1_tls_provider_abi.py"
)
NATIVE_TLS_DIR = REPO / "pcc" / "gateway" / "native"


class ScriptedTestProvider:
    """Provider-shape oracle only: no encryption, authentication or TLS wire."""

    name = "scripted-test-only"
    abi_version = TLS_PROVIDER_ABI_VERSION
    link_boundary = "python-test-object:no-native-link"
    license_id = "test-code-only"
    security_boundary = "no-crypto:provider-abi-state-oracle"
    production_ready = False

    def __init__(self) -> None:
        self.handshake_script = [
            TlsResult(TLS_WANT_READ),
            TlsResult(TLS_SELECT_SNI, server_name="api.example.test"),
            TlsResult(TLS_WANT_WRITE),
            TlsResult(TLS_OK),
        ]
        self.read_script = [TlsResult(TLS_WANT_READ), TlsResult(TLS_OK, 2)]
        self.write_script = [TlsResult(TLS_WANT_WRITE), TlsResult(TLS_OK, 3)]
        self.close_script = [
            TlsResult(TLS_WANT_WRITE),
            TlsResult(TLS_WANT_READ),
            TlsResult(TLS_CLOSED),
        ]
        self.selected_protocol = "http/1.1"
        self.contexts_created = []
        self.contexts_freed = []
        self.connections_created = 0
        self.connections_freed = 0
        self.installed_contexts = []
        self.fail_new_connection = False
        self.raise_free_connection = False
        self.raise_free_context = False
        self.close_calls = 0
        self.context_count_at_close = []

    def create_server_context(self, config, certificate):
        context = {"certificate_id": certificate.identifier}
        self.contexts_created.append(certificate.identifier)
        return context

    def new_connection(self, context, fd):
        if self.fail_new_connection:
            return None
        self.connections_created += 1
        return {
            "fd": fd,
            "context": context,
            "handshake": list(self.handshake_script),
            "read": list(self.read_script),
            "write": list(self.write_script),
            "close": list(self.close_script),
        }

    def handshake(self, session):
        return session["handshake"].pop(0)

    def set_server_context(self, session, context):
        session["context"] = context
        self.installed_contexts.append(context["certificate_id"])
        return TlsResult(TLS_OK)

    def selected_alpn(self, session):
        return self.selected_protocol

    def read(self, session, output, limit):
        result = session["read"].pop(0)
        if result.status == TLS_OK:
            output[0:2] = b"ok"
        return result

    def write(self, session, data, length):
        return session["write"].pop(0)

    def close_notify(self, session):
        return session["close"].pop(0)

    def free_connection(self, session):
        self.connections_freed += 1
        if self.raise_free_connection:
            raise RuntimeError("scripted connection cleanup failure")

    def free_context(self, context):
        self.contexts_freed.append(context["certificate_id"])
        if self.raise_free_context:
            raise RuntimeError("scripted context cleanup failure")

    def close(self):
        self.close_calls += 1
        self.context_count_at_close.append(len(self.contexts_freed))


def tls_config(reject_unknown_sni: bool = True) -> TlsConfig:
    return TlsConfig(
        default_certificate=TlsCertificate(
            "default", "default-cert-ref", "default-key-ref"
        ),
        sni_certificates=(
            TlsCertificate(
                "api", "api-cert-ref", "api-key-ref", ("api.example.test",)
            ),
            TlsCertificate(
                "wildcard",
                "wildcard-cert-ref",
                "wildcard-key-ref",
                ("*.service.example.test",),
            ),
        ),
        alpn=("h2", "http/1.1"),
        reject_unknown_sni=reject_unknown_sni,
    )


def registry_with(provider: ScriptedTestProvider) -> TlsProviderRegistry:
    registry = TlsProviderRegistry()
    registry.register(provider)
    return registry


def complete_handshake(channel: TlsChannel) -> None:
    first = channel.handshake(10, 100)
    assert first.status == TLS_WANT_READ
    assert first.wait_interest == TLS_INTEREST_READ
    selected = channel.handshake(11, 100)
    assert selected.status == TLS_SELECT_SNI
    assert selected.wait_interest == 0
    assert selected.server_name == "api.example.test"
    third = channel.handshake(12, 100)
    assert third.status == TLS_WANT_WRITE
    assert third.wait_interest == TLS_INTEREST_WRITE
    assert channel.handshake(13, 100).status == TLS_OK


def test_registry_snapshots_named_provider_and_rejects_test_provider_for_listener() -> None:
    provider = ScriptedTestProvider()
    registry = registry_with(provider)
    assert registry.get(provider.name) is provider
    info = registry.info(provider.name)
    assert info.name == "scripted-test-only"
    assert info.abi_version == TLS_PROVIDER_ABI_VERSION
    assert info.link_boundary == "python-test-object:no-native-link"
    assert info.security_boundary == "no-crypto:provider-abi-state-oracle"
    assert not info.production_ready
    with pytest.raises(TlsProviderError, match="test-only"):
        registry.get(provider.name, require_production=True)


def test_production_adapter_is_named_and_fails_closed_before_native_probe() -> None:
    with pytest.raises(TlsProviderError, match="absolute"):
        PccNativeTlsProvider("relative/provider.so", "a" * 64)
    with pytest.raises(TlsProviderError, match="expected SHA-256"):
        PccNativeTlsProvider("/opt/pcc/lib/pcc-tls-provider.so", "A" * 64)
    provider = PccNativeTlsProvider(
        "/opt/pcc/lib/pcc-tls-provider.so",
        "a" * 64,
    )
    assert provider.name == PCC_NATIVE_TLS_PROVIDER_NAME
    assert provider.production_ready
    assert provider.native_abi == "pcc-tls-native-provider-v1"
    assert "no-python-ssl" in provider.link_boundary
    assert "no-libpython" in provider.link_boundary
    assert not provider.activated
    assert provider.capabilities == 0
    assert provider.expected_library_sha256 == "a" * 64
    assert provider.verified_library_sha256 == ""
    registry = TlsProviderRegistry()
    registry.register(provider)
    with pytest.raises(TlsProviderError, match="hashing failed"):
        TlsGenerationManager(
            registry,
            provider.name,
            TlsConfig("cert.pem", "key.pem"),
            require_production=True,
        )


def test_native_provider_header_and_adapter_freeze_one_capability_checked_abi() -> None:
    header = (
        REPO / "pcc" / "gateway" / "include" / "pcc_tls_provider_v1.h"
    ).read_text(encoding="utf-8")
    source = (REPO / "pcc" / "gateway" / "tls.py").read_text(
        encoding="utf-8"
    )
    assert "pcc_tls_provider_v1_call" in header
    assert "PCC_TLS_PROVIDER_V1_REQUEST_BYTES 160" in header
    assert "PCC_TLS_V1_ASSERT_OFFSET(input3_len, 152)" in header
    assert "PCC_TLS_CAP_NONBLOCKING" in header
    assert "PCC_TLS_CAP_CLOSE_NOTIFY" in header
    assert PCC_TLS_REQUIRED_CAPABILITIES == 255
    assert "import ssl" not in source
    assert "from ssl" not in source
    assert "dynamic_library_open" in source
    assert "_pcc_sha256_file_hex_bounded" in source
    assert source.index("_pcc_sha256_file_hex_bounded") < source.index(
        "dynamic_library_open(_py_str_utf8(library_path))"
    )
    assert "library_path = self.library_path" in source
    assert "hashlib" not in source
    assert "missing required capabilities" in source


def test_provider_digest_reader_is_pcc_owned_streaming_and_total_bounded() -> None:
    pcc_runtime = (
        REPO / "pcc" / "py_runtime" / "py" / "py_http_runtime.py"
    ).read_text(encoding="utf-8")
    c_oracle = (REPO / "pcc" / "py_runtime" / "src" / "py_http.c").read_text(
        encoding="utf-8"
    )
    native_os = (
        REPO / "pcc" / "py_frontend" / "codegen" / "native_os.py"
    ).read_text(encoding="utf-8")
    runtime_abi = (
        REPO / "pcc" / "py_frontend" / "codegen" / "runtime_abi.py"
    ).read_text(encoding="utf-8")

    pcc_bounded = pcc_runtime[
        pcc_runtime.index("def _sha256_file_hex_bounded(") :
        pcc_runtime.index("def _starts_with(")
    ]
    c_bounded = c_oracle[
        c_oracle.index("static PyObject *sha256_file_hex_bounded(") :
        c_oracle.index("static int parse_http_url(")
    ]
    assert "buffer = stack_alloc(32768)" in pcc_bounded
    assert "read_limit = remaining + 1" in pcc_bounded
    assert "if count > remaining:" in pcc_bounded
    assert pcc_bounded.index("if count > remaining:") < pcc_bounded.index(
        "_sha256_update(context, buffer, count)"
    )
    assert "unsigned char buffer[32768]" in c_bounded
    assert "read_cap = (size_t)remaining + 1U" in c_bounded
    assert "count > (size_t)remaining" in c_bounded
    assert "py_sha256_file_hex_bounded" in native_os
    bounded_lowering = native_os[
        native_os.index('if name == "_pcc_sha256_file_hex_bounded"') :
        native_os.index("        return None", native_os.index(
            'if name == "_pcc_sha256_file_hex_bounded"'
        ))
    ]
    assert "_enter_container_temp_root" in bounded_lowering
    assert "rooted_pcc_lifetimes=" in bounded_lowering
    assert "pcc_gc_load_ptr" in bounded_lowering
    assert "_release_rooted_pcc_lifetimes" in bounded_lowering
    assert "_emit_post_call_err_check" in bounded_lowering
    assert 'self.runtime["pcc_gc_pin"]' in bounded_lowering
    assert "pinned_release_on_error=((result, True),)" in bounded_lowering
    assert 'self.runtime["pcc_gc_unpin"]' in bounded_lowering
    assert '"py_sha256_file_hex_bounded": (_PYOBJ, [_PYOBJ, _I64], False)' in (
        runtime_abi
    )


def test_provider_digest_native_result_has_exact_owned_classification() -> None:
    native_modules = (
        REPO / "pcc" / "py_frontend" / "codegen" / "native_modules.py"
    ).read_text(encoding="utf-8")
    native_classifier = native_modules[
        native_modules.index("    def _native_builtin_value_kind_for_expr(") :
        native_modules.index("    def _emit_native_builtin_value_call(")
    ]
    ownership = (
        REPO / "pcc" / "py_frontend" / "codegen" / "ownership_lowering.py"
    ).read_text(encoding="utf-8")
    object_classifier = ownership[
        ownership.index("    def _expr_returns_owned_object(") :
        ownership.index("    def _return_type_is_owned_object(")
    ]
    raw_classifier = ownership[
        ownership.index("    def _raw_scaffold_object_rhs_is_owned(") :
        ownership.index("    def _valueclass_payload_expr_fields_are_owned(")
    ]

    for operation in (
        "os._pcc_sha256_file_hex",
        "os._pcc_sha256_file_hex_bounded",
    ):
        # The native-module classifier deliberately stores the shared ``os.``
        # prefix once and keeps only the attribute inventory as literals.
        # Ownership classifiers consume its fully-qualified return value.
        assert operation.split(".", 1)[1] in native_classifier
        assert operation in object_classifier
        assert operation in raw_classifier
    assert 'return "os." + expr.name' in native_classifier
    assert raw_classifier.index("native_call =") < raw_classifier.index(
        "if self._module_has_c_abi_export:"
    )


def test_native_provider_digest_mismatch_never_reaches_dynamic_loader(
    monkeypatch,
) -> None:
    opened = []
    monkeypatch.setattr(
        tls_module.os,
        "_pcc_sha256_file_hex_bounded",
        lambda path, max_bytes: "b" * 64,
        raising=False,
    )
    monkeypatch.setattr(
        tls_module,
        "dynamic_library_open",
        lambda path: opened.append(path),
    )
    provider = PccNativeTlsProvider(
        "/opt/pcc/lib/pcc-tls-provider.so",
        "a" * 64,
        4096,
    )

    with pytest.raises(TlsProviderError, match="SHA-256 mismatch"):
        provider.activate()
    assert opened == []
    assert provider.verified_library_sha256 == ""


def test_native_provider_hashes_with_configured_bound_before_open(
    monkeypatch,
) -> None:
    operations = []

    def digest(path, max_bytes):
        operations.append(("hash", path, max_bytes))
        return "c" * 64

    def open_library(path):
        operations.append(("open", path))
        return None

    monkeypatch.setattr(
        tls_module.os,
        "_pcc_sha256_file_hex_bounded",
        digest,
        raising=False,
    )
    # CPython executes the host model in this test. The compiled provider
    # route lowers this extern to the raw UTF-8 pointer required by dlopen;
    # model that ABI conversion alongside the injected loader.
    monkeypatch.setattr(tls_module, "_py_str_utf8", lambda value: value)
    monkeypatch.setattr(tls_module, "dynamic_library_open", open_library)
    provider = PccNativeTlsProvider(
        "/opt/pcc/lib/pcc-tls-provider.so",
        "c" * 64,
        8192,
    )

    with pytest.raises(TlsProviderError, match="could not be opened"):
        provider.activate()
    assert operations[0] == (
        "hash",
        "/opt/pcc/lib/pcc-tls-provider.so",
        8192,
    )
    assert operations[1][0] == "open"


def test_openssl_provider_source_freezes_crypto_and_ownership_boundary() -> None:
    provider_source = (NATIVE_TLS_DIR / "openssl_provider.c").read_text(
        encoding="utf-8"
    )
    build_source = (NATIVE_TLS_DIR / "Makefile").read_text(encoding="utf-8")
    readme = (NATIVE_TLS_DIR / "README.md").read_text(encoding="utf-8")
    manifest = json.loads(
        (NATIVE_TLS_DIR / "provider-manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["provider_name"] == PCC_NATIVE_TLS_PROVIDER_NAME
    assert manifest["implementation_id"] == "pcc-openssl-3-provider-v1"
    assert manifest["entry_symbol"] == "pcc_tls_provider_v1_call"
    assert manifest["adapter_license"] == "MIT"
    assert manifest["crypto_dependency"] == {
        "name": "OpenSSL",
        "minimum_version": "3.0.0",
        "license": "Apache-2.0",
        "vendored": False,
        "runtime_abi_checked": True,
    }
    assert manifest["link_boundary"] == {
        "libpython": False,
        "host_python_interpreter": False,
        "platform_c_abi_and_libc": True,
        "zero_libc_claim": False,
    }
    assert manifest["runtime_prerequisites"] == {
        "socket_is_nonblocking": True,
        "socket_fd_remains_pcc_owned": True,
        "process_or_socket_owner_suppresses_sigpipe": True,
    }
    assert not manifest["distribution"]["runtime_artifact_digest_authenticated"]
    assert manifest["runtime_artifact_authentication"]["algorithm"] == "sha256"
    assert manifest["runtime_artifact_authentication"][
        "pre_open_path_digest_verified"
    ]
    assert (
        manifest["runtime_artifact_authentication"]["default_max_artifact_bytes"]
        == 268435456
    )
    assert not manifest["runtime_artifact_authentication"][
        "concurrent_path_replacement_closed"
    ]
    assert manifest["certificate_inputs"]["loaded_during_generation_creation"]
    assert not manifest["certificate_inputs"]["paths_retained_after_load"]

    for operation in (
        "PROBE",
        "CONTEXT_CREATE",
        "CONTEXT_FREE",
        "CONNECTION_CREATE",
        "CONNECTION_FREE",
        "HANDSHAKE",
        "SET_CONTEXT",
        "SELECTED_ALPN",
        "READ",
        "WRITE",
        "CLOSE_NOTIFY",
    ):
        assert "case PCC_TLS_OP_" + operation in provider_source
    for required_api in (
        "SSL_CTX_use_certificate_chain_file",
        "SSL_CTX_use_PrivateKey_file",
        "SSL_CTX_set_client_hello_cb",
        "SSL_CLIENT_HELLO_RETRY",
        "SSL_ERROR_WANT_CLIENT_HELLO_CB",
        "SSL_set_SSL_CTX",
        "SSL_CTX_set_alpn_select_cb",
        "SSL_read_ex",
        "SSL_write_ex",
        "SSL_shutdown",
        "SSL_R_UNEXPECTED_EOF_WHILE_READING",
        "pcc_tls_reject_private_key_password",
    ):
        assert required_api in provider_source
    for forbidden_call in ("poll", "select", "epoll_wait", "kevent", "sleep"):
        assert re.search(r"\b" + forbidden_call + r"\s*\(", provider_source) is None
    assert "#include <Python.h>" not in provider_source
    assert re.search(r"\bPy_[A-Za-z0-9_]+", provider_source) is None
    assert "OPENSSL_VERSION_MAJOR < 3" in provider_source
    assert "OPENSSL_IS_BORINGSSL" in provider_source
    assert "LIBRESSL_VERSION_NUMBER" in provider_source
    assert "OPENSSL_PREFIX" in build_source
    assert "OPENSSL_RUNTIME_PATH_FLAGS" in build_source
    assert "-fvisibility=hidden" in build_source
    assert "minimum_openssl=3.0.0" in build_source
    assert "not evidence" in readme
    assert "zero-libc" in readme


class _NativeTlsSecondary(ctypes.Union):
    _fields_ = [("pointer", ctypes.c_void_p), ("integer", ctypes.c_int64)]


class _NativeTlsRequest(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint64),
        ("abi_version", ctypes.c_uint64),
        ("operation", ctypes.c_int64),
        ("status", ctypes.c_int64),
        ("error_code", ctypes.c_int64),
        ("primary", ctypes.c_void_p),
        ("secondary", _NativeTlsSecondary),
        ("input0", ctypes.c_void_p),
        ("input0_len", ctypes.c_uint64),
        ("input1", ctypes.c_void_p),
        ("input1_len", ctypes.c_uint64),
        ("input2", ctypes.c_void_p),
        ("input2_len", ctypes.c_uint64),
        ("output0", ctypes.c_void_p),
        ("output0_capacity", ctypes.c_uint64),
        ("output0_len", ctypes.c_uint64),
        ("flags", ctypes.c_uint64),
        ("provider_code", ctypes.c_int64),
        ("input3", ctypes.c_void_p),
        ("input3_len", ctypes.c_uint64),
    ]


@pytest.mark.integration
@pytest.mark.pcc_gate(
    env="PCC_RUN_OPENSSL_TLS_PROVIDER",
    unavailable=(
        None
        if sys.platform in ("darwin", "linux")
        else "the v1 provider build supports Darwin and Linux only"
    ),
)
def test_openssl_provider_build_and_host_loader_probe(tmp_path: Path) -> None:
    """Build/load the real provider; this ABI probe is not an HTTPS claim."""

    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    command = [
        environment.get("MAKE", "make"),
        "-C",
        str(NATIVE_TLS_DIR),
        "OUT_DIR=" + str(tmp_path),
    ]
    openssl_prefix = environment.get("PCC_OPENSSL_PREFIX", "").strip()
    if openssl_prefix:
        command.append("OPENSSL_PREFIX=" + openssl_prefix)
    built = subprocess.run(
        command,
        cwd=REPO,
        env=environment,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert built.returncode == 0, built.stdout + built.stderr

    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    library_path = tmp_path / ("libpcc_tls_openssl" + suffix)
    assert library_path.is_file()
    library = ctypes.CDLL(str(library_path))
    call = library.pcc_tls_provider_v1_call
    call.argtypes = [ctypes.c_int64, ctypes.POINTER(_NativeTlsRequest)]
    call.restype = ctypes.c_int64

    assert ctypes.sizeof(_NativeTlsRequest) == 160
    identity = ctypes.create_string_buffer(65)
    request = _NativeTlsRequest()
    request.struct_size = ctypes.sizeof(request)
    request.abi_version = 1
    request.operation = 0
    request.output0 = ctypes.cast(identity, ctypes.c_void_p)
    request.output0_capacity = 64
    assert call(0, ctypes.byref(request)) == TLS_OK
    assert request.status == TLS_OK
    assert request.error_code == 0
    assert identity.raw[: request.output0_len] == b"pcc-openssl-3-provider-v1"
    assert request.flags & PCC_TLS_REQUIRED_CAPABILITIES == (
        PCC_TLS_REQUIRED_CAPABILITIES
    )


def test_registry_rejects_unnamed_versionless_or_unlabeled_provider() -> None:
    provider = ScriptedTestProvider()
    provider.name = "Bad Provider"
    with pytest.raises(TlsProviderError, match="name"):
        TlsProviderRegistry().register(provider)

    provider = ScriptedTestProvider()
    provider.abi_version = 99
    with pytest.raises(TlsProviderError, match="ABI"):
        TlsProviderRegistry().register(provider)

    provider = ScriptedTestProvider()
    provider.link_boundary = ""
    with pytest.raises(TlsProviderError, match="link_boundary"):
        TlsProviderRegistry().register(provider)


def test_sni_selection_prefers_exact_then_one_label_wildcard_and_can_reject() -> None:
    config = tls_config()
    assert config.select_certificate("").identifier == "default"
    assert config.select_certificate("API.EXAMPLE.TEST.").identifier == "api"
    assert config.select_certificate("a.service.example.test").identifier == "wildcard"
    assert config.select_certificate("a.b.service.example.test") is None
    assert config.select_certificate("unknown.example.test") is None
    fallback = tls_config(reject_unknown_sni=False)
    assert fallback.select_certificate("unknown.example.test").identifier == "default"


def test_config_rejects_ambiguous_names_keys_and_alpn() -> None:
    with pytest.raises(ValueError, match="required"):
        TlsConfig()
    with pytest.raises(ValueError, match="wildcard"):
        TlsCertificate("bad", "cert", "key", ("*",))
    duplicate = TlsCertificate("one", "cert", "key", ("same.example.test",))
    second = TlsCertificate("two", "cert", "key", ("same.example.test",))
    with pytest.raises(ValueError, match="duplicate SNI"):
        TlsConfig(
            default_certificate=TlsCertificate("default", "cert", "key"),
            sni_certificates=(duplicate, second),
        )
    with pytest.raises(ValueError, match="duplicate ALPN"):
        TlsConfig("cert", "key", alpn=("h2", "h2"))
    with pytest.raises(ValueError, match="client CA"):
        TlsConfig("cert", "key", require_client_certificate=True)


def test_nonblocking_handshake_io_sni_alpn_and_close_notify() -> None:
    provider = ScriptedTestProvider()
    generation = TlsGeneration(1, provider, tls_config())
    channel = TlsChannel(generation, 7)
    assert generation.references == 2
    assert generation.release() == 0

    complete_handshake(channel)
    assert channel.handshake_complete
    assert channel.server_name == "api.example.test"
    assert channel.certificate_id == "api"
    assert channel.alpn == "http/1.1"
    assert provider.installed_contexts == ["api"]

    output = bytearray(8)
    first_read = channel.read(output, 8, 20, 100)
    assert first_read.status == TLS_WANT_READ
    assert first_read.wait_interest == TLS_INTEREST_READ
    second_read = channel.read(output, 8, 21, 100)
    assert second_read.status == TLS_OK and second_read.count == 2
    assert bytes(output[:2]) == b"ok"

    first_write = channel.write(b"abc", 3, 30, 100)
    assert first_write.status == TLS_WANT_WRITE
    assert first_write.wait_interest == TLS_INTEREST_WRITE
    second_write = channel.write(b"abc", 3, 31, 100)
    assert second_write.status == TLS_OK and second_write.count == 3

    assert channel.close_notify(40, 100).status == TLS_WANT_WRITE
    assert channel.close_notify(41, 100).status == TLS_WANT_READ
    assert channel.close_notify(42, 100).status == TLS_CLOSED
    assert channel.close_notify_complete
    assert channel.close().status == TLS_CLOSED
    assert channel.close().status == TLS_CLOSED
    assert provider.connections_freed == 1
    assert sorted(provider.contexts_freed) == ["api", "default", "wildcard"]


def test_absolute_deadline_fails_before_calling_provider() -> None:
    provider = ScriptedTestProvider()
    generation = TlsGeneration(1, provider, tls_config())
    channel = TlsChannel(generation, 8)
    calls_before = len(channel.session["handshake"])
    result = channel.handshake(100, 100)
    assert result.status == TLS_ERROR
    assert result.error_code == TLS_ERR_DEADLINE
    assert result.error_name == "deadline"
    assert len(channel.session["handshake"]) == calls_before
    generation.retire()
    channel.close()


def test_provider_protocol_error_is_stable_and_detail_is_bounded() -> None:
    provider = ScriptedTestProvider()
    provider.handshake_script = [
        TlsResult(TLS_ERROR, error_code=TLS_ERR_PROTOCOL, detail="x" * 400)
    ]
    generation = TlsGeneration(1, provider, tls_config())
    channel = TlsChannel(generation, 9)
    result = channel.handshake(1, 20)
    assert result.status == TLS_ERROR
    assert result.error_code == TLS_ERR_PROTOCOL
    assert result.error_name == "protocol"
    assert len(result.detail) == 160
    generation.retire()
    channel.close()


def test_provider_result_shape_and_alpn_are_fail_closed() -> None:
    provider = ScriptedTestProvider()
    provider.handshake_script = [TlsResult(TLS_OK, count=1)]
    generation = TlsGeneration(1, provider, tls_config())
    channel = TlsChannel(generation, 10)
    result = channel.handshake(1, 20)
    assert result.error_code == TLS_ERR_PROVIDER_CONTRACT
    generation.retire()
    channel.close()

    provider = ScriptedTestProvider()
    provider.handshake_script = [TlsResult(TLS_OK)]
    provider.selected_protocol = "acme-unsupported"
    generation = TlsGeneration(2, provider, tls_config())
    channel = TlsChannel(generation, 11)
    result = channel.handshake(1, 20)
    assert result.error_code == TLS_ERR_ALPN
    generation.retire()
    channel.close()


def test_unknown_sni_maps_to_stable_error_without_context_install() -> None:
    provider = ScriptedTestProvider()
    provider.handshake_script = [
        TlsResult(TLS_SELECT_SNI, server_name="unknown.example.test")
    ]
    generation = TlsGeneration(1, provider, tls_config())
    channel = TlsChannel(generation, 12)
    result = channel.handshake(1, 20)
    assert result.error_code == TLS_ERR_UNRECOGNIZED_NAME
    assert result.error_name == "unrecognized-name"
    assert provider.installed_contexts == []
    generation.retire()
    channel.close()


def test_reload_keeps_old_contexts_until_old_channels_release() -> None:
    provider = ScriptedTestProvider()
    manager = TlsGenerationManager(
        registry_with(provider), provider.name, tls_config(), require_production=False
    )
    first_generation = manager.active
    first_channel = manager.new_channel(20)
    replacement = manager.reload(tls_config(reject_unknown_sni=False))
    assert replacement.generation_id == 2
    assert first_generation.retired
    assert first_generation.references == 1
    assert provider.contexts_freed == []

    second_channel = manager.new_channel(21)
    assert manager.close() == 0
    assert replacement.retired and replacement.references == 1
    first_channel.close()
    assert len(provider.contexts_freed) == 3
    second_channel.close()
    assert len(provider.contexts_freed) == 6
    assert manager.close() == 0
    assert provider.connections_freed == 2


def test_generation_multi_carrier_lifetime_is_exactly_once() -> None:
    provider = ScriptedTestProvider()
    generation = TlsGeneration(1, provider, tls_config())
    worker_count = 12
    retained = Barrier(worker_count + 1)
    release = Barrier(worker_count + 1)
    errors = []
    selections = []

    def use_generation() -> None:
        try:
            generation.retain()
            context, identifier = generation.select_context("api.example.test")
            selections.append((context["certificate_id"], identifier))
            retained.wait(timeout=5)
            release.wait(timeout=5)
            generation.release()
        except Exception as error:
            errors.append(error)

    workers = [Thread(target=use_generation) for _index in range(worker_count)]
    for worker in workers:
        worker.start()
    retained.wait(timeout=5)

    retire_results = []

    def retire_generation() -> None:
        try:
            retire_results.append(generation.retire())
        except Exception as error:
            errors.append(error)

    retirees = [Thread(target=retire_generation) for _index in range(8)]
    for retiree in retirees:
        retiree.start()
    for retiree in retirees:
        retiree.join(timeout=5)
        assert not retiree.is_alive()

    assert generation.retired
    assert generation.references == worker_count
    assert retire_results == [0] * 8
    assert provider.contexts_freed == []
    release.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()

    assert errors == []
    assert selections == [("api", "api")] * worker_count
    assert generation.references == 0
    assert generation.destroyed
    assert sorted(provider.contexts_freed) == ["api", "default", "wildcard"]


def test_channel_concurrent_close_releases_session_and_generation_once() -> None:
    provider = ScriptedTestProvider()
    generation = TlsGeneration(1, provider, tls_config())
    channel = TlsChannel(generation, 25)
    assert generation.retire() == 0
    statuses = []

    def close_channel() -> None:
        statuses.append(channel.close().status)

    workers = [Thread(target=close_channel) for _index in range(12)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()

    assert statuses == [TLS_CLOSED] * 12
    assert provider.connections_freed == 1
    assert sorted(provider.contexts_freed) == ["api", "default", "wildcard"]
    assert generation.destroyed


def test_external_registry_remains_reusable_after_each_manager_closes() -> None:
    provider = ScriptedTestProvider()
    registry = registry_with(provider)
    first = TlsGenerationManager(
        registry, provider.name, tls_config(), require_production=False
    )
    second = TlsGenerationManager(
        registry, provider.name, tls_config(), require_production=False
    )

    assert first.close() == 1
    assert second.close() == 1
    assert provider.close_calls == 0

    third = TlsGenerationManager(
        registry, provider.name, tls_config(), require_production=False
    )
    assert third.close() == 1
    assert provider.close_calls == 0
    assert registry.get(provider.name) is provider


def test_manager_owned_registry_closes_provider_once_after_final_manager() -> None:
    provider = ScriptedTestProvider()
    registry = TlsProviderRegistry(
        owns_providers=True,
        close_on_last_manager=True,
    )
    registry.register(provider)
    first = TlsGenerationManager(
        registry, provider.name, tls_config(), require_production=False
    )
    second = TlsGenerationManager(
        registry, provider.name, tls_config(), require_production=False
    )

    start = Barrier(3)
    close_results = []

    def close_manager(manager) -> None:
        start.wait(timeout=5)
        close_results.append(manager.close())

    workers = [
        Thread(target=close_manager, args=(first,)),
        Thread(target=close_manager, args=(second,)),
    ]
    for worker in workers:
        worker.start()
    start.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()

    assert sorted(close_results) == [1, 1]
    assert provider.close_calls == 1
    assert provider.context_count_at_close == [6]
    assert second.close() == 0
    assert registry.close() == 0
    assert provider.close_calls == 1
    with pytest.raises(TlsProviderError, match="closed"):
        registry.get(provider.name)


def test_concurrent_reload_and_close_leave_every_created_context_released() -> None:
    provider = ScriptedTestProvider()
    manager = TlsGenerationManager(
        registry_with(provider),
        provider.name,
        tls_config(),
        require_production=False,
    )
    start = Barrier(10)
    unexpected = []

    def reload_manager() -> None:
        try:
            start.wait(timeout=5)
            manager.reload(tls_config(reject_unknown_sni=False))
        except TlsProviderError:
            # Closing may win publication; fail-closed rejection is expected.
            pass
        except Exception as error:
            unexpected.append(error)

    def close_manager() -> None:
        try:
            start.wait(timeout=5)
            manager.close()
        except Exception as error:
            unexpected.append(error)

    workers = [Thread(target=reload_manager) for _index in range(8)]
    workers.append(Thread(target=close_manager))
    for worker in workers:
        worker.start()
    start.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()

    assert unexpected == []
    assert manager.closed
    assert manager.close() == 0
    assert sorted(provider.contexts_freed) == sorted(provider.contexts_created)


def test_native_provider_close_waits_for_resources_and_inflight_calls(
    monkeypatch,
) -> None:
    closed_libraries = []
    monkeypatch.setattr(
        tls_module,
        "dynamic_library_close",
        lambda library: closed_libraries.append(library),
    )
    provider = PccNativeTlsProvider(
        "/opt/pcc/lib/pcc-tls-provider.so",
        "a" * 64,
    )
    library = object()
    provider.library_handle = library
    provider.entrypoint = object()
    provider.activated = True
    provider._active_contexts = 1
    provider._active_sessions = 1
    provider._active_calls = 1

    assert provider.close() == 0
    assert not provider.closed
    assert closed_libraries == []
    provider._release_resource("session")
    provider._release_resource("context")
    assert not provider.closed
    assert closed_libraries == []
    provider._finish_call()
    assert provider.closed
    assert closed_libraries == [library]
    assert provider.close() == 0
    assert closed_libraries == [library]


def test_connection_creation_failure_restores_generation_reference() -> None:
    provider = ScriptedTestProvider()
    provider.fail_new_connection = True
    generation = TlsGeneration(1, provider, tls_config())
    with pytest.raises(TlsProviderError, match="create connection"):
        TlsChannel(generation, 22)
    assert generation.references == 1
    assert generation.retire() == 1
    assert len(provider.contexts_freed) == 3


def test_cleanup_is_exactly_once_even_when_provider_cleanup_raises() -> None:
    provider = ScriptedTestProvider()
    provider.raise_free_connection = True
    provider.raise_free_context = True
    generation = TlsGeneration(1, provider, tls_config())
    channel = TlsChannel(generation, 23)
    generation.retire()
    result = channel.close()
    assert result.status == TLS_ERROR
    assert result.error_code == TLS_ERR_PROVIDER
    assert provider.connections_freed == 1
    assert len(provider.contexts_freed) == 3
    assert channel.close().status == TLS_CLOSED
    assert provider.connections_freed == 1
    assert len(provider.contexts_freed) == 3


def test_cancel_is_terminal_and_maps_stably() -> None:
    provider = ScriptedTestProvider()
    generation = TlsGeneration(1, provider, tls_config())
    channel = TlsChannel(generation, 24)
    result = channel.cancel()
    assert result.error_code == TLS_ERR_CANCELLED
    assert tls_error_name(result.error_code) == "cancelled"
    assert channel.handshake(1, 20) is result
    generation.retire()
    channel.close()


@pytest.mark.integration
@pytest.mark.pcc_gate(probe="pcc1")
def test_current_pcc1_self_no_libpython_tls_provider_abi_fixture(
    tmp_path: Path,
    pcc_py_runtime_archive: Path,
) -> None:
    """Compile/run the ABI state model; this is explicitly not an HTTPS gate."""

    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the TLS provider ABI gate")
    executable = tmp_path / "current_pcc1_tls_provider_abi"
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
            str(PCC1_TLS_ABI_SOURCE),
            "-o",
            str(executable),
        ],
        cwd=REPO,
        env=environment,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run(
        [str(executable)],
        cwd=REPO,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout.strip() == (
        "PCC1_TLS_PROVIDER_ABI_ONLY_OK "
        "scripted-pcc1-test-only python-object:no-crypto-link"
    )


def test_live_https_canary_is_owned_by_the_product_harness() -> None:
    """The former reserved skip is now a separately gated real-wire test."""

    harness = REPO / "tests" / "python" / "test_gateway_product_canary.py"
    source = harness.read_text(encoding="utf-8")
    assert "PCC_RUN_GATEWAY_PRODUCT_CANARY" in source
    assert '"--backend",\n        "self"' in source
    assert '"--python-libpython=off"' in source
    assert '("0", "1", "2", "3", "4")' in source
    assert "VerifiedHttpsConnection" in source
    assert "signal.SIGHUP" in source
    assert "signal.SIGTERM" in source
    assert "pytest.skip" not in source
