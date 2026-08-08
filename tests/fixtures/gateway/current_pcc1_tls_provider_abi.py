"""Current-pcc1 TLS provider ABI acceptance source.

This provider is a deterministic state-machine oracle.  It performs no TLS
wire processing, encryption, certificate validation or authentication and is
therefore permanently ``production_ready = False``.  Its output marker says
``ABI_ONLY`` so it cannot honestly be cited as an HTTPS result.
"""

from pcc.gateway.tls import (
    TLS_CLOSED,
    TLS_OK,
    TLS_PROVIDER_ABI_VERSION,
    TLS_SELECT_SNI,
    TLS_WANT_READ,
    TlsCertificate,
    TlsConfig,
    TlsGenerationManager,
    TlsProviderRegistry,
    TlsResult,
)


class Pcc1ScriptedProvider:
    name = "scripted-pcc1-test-only"
    abi_version = TLS_PROVIDER_ABI_VERSION
    link_boundary = "python-object:no-crypto-link"
    license_id = "test-code-only"
    security_boundary = "no-crypto:abi-state-oracle"
    production_ready = False

    def __init__(self) -> None:
        self.connection_frees = 0
        self.context_frees = 0

    def create_server_context(self, config, certificate):
        return {"certificate_id": certificate.identifier}

    def new_connection(self, context, fd):
        return {"context": context, "fd": fd, "step": 0}

    def handshake(self, session):
        step = session["step"]
        session["step"] = step + 1
        if step == 0:
            return TlsResult(TLS_SELECT_SNI, server_name="api.example.test")
        if step == 1:
            return TlsResult(TLS_WANT_READ)
        return TlsResult(TLS_OK)

    def set_server_context(self, session, context):
        session["context"] = context
        return TlsResult(TLS_OK)

    def selected_alpn(self, session):
        return "http/1.1"

    def read(self, session, output, limit):
        return TlsResult(TLS_WANT_READ)

    def write(self, session, data, length):
        return TlsResult(TLS_OK, count=length)

    def close_notify(self, session):
        return TlsResult(TLS_CLOSED)

    def free_connection(self, session):
        self.connection_frees += 1

    def free_context(self, context):
        self.context_frees += 1


def main() -> int:
    provider = Pcc1ScriptedProvider()
    registry = TlsProviderRegistry()
    registry.register(provider)
    config = TlsConfig(
        default_certificate=TlsCertificate(
            "default", "opaque-default-cert", "opaque-default-key"
        ),
        sni_certificates=(
            TlsCertificate(
                "api",
                "opaque-api-cert",
                "opaque-api-key",
                ("api.example.test",),
            ),
        ),
        alpn=("http/1.1",),
        reject_unknown_sni=True,
    )
    manager = TlsGenerationManager(
        registry, provider.name, config, require_production=False
    )
    channel = manager.new_channel(7)
    selected = channel.handshake(1, 50)
    if selected.status != TLS_SELECT_SNI or channel.certificate_id != "api":
        return 1
    if channel.handshake(2, 50).status != TLS_WANT_READ:
        return 2
    if channel.handshake(3, 50).status != TLS_OK:
        return 3
    if channel.alpn != "http/1.1":
        return 4
    if channel.write(b"pcc1", 4, 4, 50).count != 4:
        return 5
    if channel.close_notify(5, 50).status != TLS_CLOSED:
        return 6
    if manager.close() != 0:
        return 7
    if channel.close().status != TLS_CLOSED:
        return 8
    if provider.connection_frees != 1 or provider.context_frees != 2:
        return 9
    print(
        "PCC1_TLS_PROVIDER_ABI_ONLY_OK "
        + provider.name
        + " "
        + provider.link_boundary
    )
    return 0


main()
