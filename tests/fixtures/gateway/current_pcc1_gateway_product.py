"""Template for the strict current-pcc1 no-nginx gateway product canary.

The pytest harness substitutes only the literal configuration tokens below,
then current pcc1 compiles this application with self/no-libpython.  All
network service behavior remains inside the emitted native process.
"""

from pcc.gateway import (
    DnsResolverConfig,
    DnsServer,
    GatewayConfig,
    GatewayServer,
    ListenerConfig,
    NativeDnsTransport,
    TlsConfig,
    UpstreamEndpoint,
    UpstreamGroup,
)
from pcc.gateway.dns import Resolver
from pcc.gateway.dns_native import LazySystemResolver
from pcc.gateway.proxy import ProxyTimeouts
from pcc.unsafe import gc_backend_current
import pcc.virtual_thread as virtual_thread
from pcc.web import App, Response, get, proxy


LISTEN_PORT = __PCC_LISTEN_PORT__
DNS_PORT = __PCC_DNS_PORT__
UPSTREAM_PORT = __PCC_UPSTREAM_PORT__
WAITSET_BACKEND = __PCC_WAITSET_BACKEND__
PROVIDER_LIBRARY = __PCC_PROVIDER_LIBRARY__
PROVIDER_LIBRARY_SHA256 = __PCC_PROVIDER_LIBRARY_SHA256__
CERTIFICATE_OLD = __PCC_CERTIFICATE_OLD__
PRIVATE_KEY_OLD = __PCC_PRIVATE_KEY_OLD__
CERTIFICATE_NEW = __PCC_CERTIFICATE_NEW__
PRIVATE_KEY_NEW = __PCC_PRIVATE_KEY_NEW__


def health(request):
    return Response.text("pcc-gateway-healthy")


def streaming(request):
    return Response.stream((b"stream-", b"from-", b"pcc1"))


def gateway_config(certificate, private_key):
    tls = TlsConfig(
        certificate=certificate,
        private_key=private_key,
        alpn=("http/1.1",),
    )
    listener = ListenerConfig(
        "127.0.0.1",
        LISTEN_PORT,
        tls_provider="pcc-native-tls-v1",
        tls_config=tls,
        tls_provider_library=PROVIDER_LIBRARY,
        tls_provider_library_sha256=PROVIDER_LIBRARY_SHA256,
    )
    return GatewayConfig(
        listeners=(listener,),
        carrier_count=2,
        drain_timeout_ms=3000,
        waitset_backend=WAITSET_BACKEND,
        control_poll_ms=5,
        install_signal_handlers=True,
    )


def reload_generation(previous):
    return gateway_config(CERTIFICATE_NEW, PRIVATE_KEY_NEW)


def main() -> int:
    upstream = UpstreamGroup(
        "backend",
        (UpstreamEndpoint("backend.pcc.test", UPSTREAM_PORT),),
        max_active=8,
        max_idle=4,
    )
    app = App(
        routes=(
            get("/health", health),
            get("/stream", streaming),
            proxy(
                "/api/{path*}",
                "backend",
                strip_prefix="/api",
                timeouts=ProxyTimeouts(
                    connect_ms=2000,
                    header_ms=5000,
                    body_ms=5000,
                    idle_ms=1000,
                ),
            ),
        ),
        upstreams=(upstream,),
    )
    dns_transport = NativeDnsTransport()
    resolver = Resolver(
        config=DnsResolverConfig(
            (DnsServer("127.0.0.1", DNS_PORT),),
            attempts_per_server=2,
            attempt_timeout_ms=500,
        ),
        query_seed=101,
    )
    server = GatewayServer(
        app,
        config=gateway_config(CERTIFICATE_OLD, PRIVATE_KEY_OLD),
        resolver=LazySystemResolver(dns_transport, resolver),
        dns_transport=dns_transport,
        reload_factory=reload_generation,
    )
    result = server.run()
    if result != 0:
        raise RuntimeError("gateway server did not stop cleanly")
    metrics = server.lifecycle.metrics
    current_generation = server.lifecycle.current
    generation_released = 0
    if current_generation.released:
        generation_released = 1
    tls_manager_closed = 0
    tls_provider_closed = 0
    tls_provider_contexts = -1
    tls_provider_sessions = -1
    if server.tls_manager is not None:
        if server.tls_manager.closed:
            tls_manager_closed = 1
        tls_provider = server.tls_manager.provider
        if tls_provider.closed:
            tls_provider_closed = 1
        tls_provider_contexts = tls_provider.active_contexts
        tls_provider_sessions = tls_provider.active_sessions
    print("PCC1_GATEWAY_GC_BACKEND", gc_backend_current())
    print("PCC1_GATEWAY_WAITSET_BACKEND", virtual_thread.io_backend())
    print("PCC1_GATEWAY_DRAIN_FORCED", metrics.get("drain_forced"))
    print(
        "PCC1_GATEWAY_RESOURCE_CLOSURE",
        upstream.active,
        len(server.proxy_pools["backend"].idle),
        current_generation.references,
        generation_released,
        len(server.connections),
        len(server.connection_owners),
        len(server.lifecycle._retired_generations),
        tls_manager_closed,
        tls_provider_closed,
        tls_provider_contexts,
        tls_provider_sessions,
    )
    print(
        "PCC1_GATEWAY_PRODUCT_OK",
        metrics.get("requests_started"),
        metrics.get("dns_queries"),
        metrics.get("tls_handshakes_completed"),
        metrics.get("tls_generation_reloads"),
        metrics.get("connections_active"),
        metrics.get("requests_active"),
        metrics.get("requests_queued"),
        metrics.get("buffered_bytes"),
        metrics.get("upstream_active"),
    )
    return result


main()
