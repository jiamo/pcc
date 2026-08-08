from pcc.gateway.config import BufferLimits, GatewayConfig, Http1Limits, ListenerConfig


def test_config_replace_builds_new_generation_record() -> None:
    first = GatewayConfig(
        listeners=(ListenerConfig("127.0.0.1", 8080),),
        carrier_count=2,
        max_requests_per_connection=77,
        write_timeout_ms=1234,
        accept_poll_ms=56,
        control_poll_ms=7,
    )
    second = first.replace(
        listeners=(ListenerConfig("127.0.0.1", 9090),),
        carrier_count=4,
    )
    assert first.listeners[0].port == 8080
    assert first.carrier_count == 2
    assert second.listeners[0].port == 9090
    assert second.carrier_count == 4
    assert second.max_requests_per_connection == 77
    assert second.write_timeout_ms == 1234
    assert second.accept_poll_ms == 56
    assert second.control_poll_ms == 7


def test_http_and_buffer_limits_reject_inconsistent_bounds() -> None:
    try:
        Http1Limits(body_bytes=100, chunk_bytes=101)
    except ValueError:
        pass
    else:
        raise AssertionError("chunk larger than body limit was accepted")
    try:
        BufferLimits(low_watermark=10, high_watermark=20, connection_bytes=19)
    except ValueError:
        return
    raise AssertionError("buffer hard limit below watermark was accepted")


def test_waitset_backend_is_explicit_and_fail_closed() -> None:
    assert GatewayConfig(waitset_backend="epoll").waitset_backend == "epoll"
    try:
        GatewayConfig(waitset_backend="event-loop-magic")
    except ValueError:
        return
    raise AssertionError("unknown waitset backend was accepted")


def test_signal_ownership_defaults_on_and_can_be_explicitly_disabled() -> None:
    config = GatewayConfig()
    assert config.install_signal_handlers is True
    assert config.replace().install_signal_handlers is True
    assert config.replace(install_signal_handlers=False).install_signal_handlers is False


def test_tls_provider_artifact_provenance_is_required_and_bounded() -> None:
    digest = "a" * 64
    listener = ListenerConfig(
        tls_provider="pcc-native-tls-v1",
        tls_config=object(),
        tls_provider_library="/opt/pcc/lib/provider.so",
        tls_provider_library_sha256=digest,
        tls_provider_max_bytes=123456,
    )
    assert listener.tls_provider_library_sha256 == digest
    assert listener.tls_provider_max_bytes == 123456

    invalid = (
        {"tls_provider_library_sha256": digest},
        {"tls_provider_library": "/provider.so"},
        {
            "tls_provider_library": "/provider.so",
            "tls_provider_library_sha256": "A" * 64,
        },
        {
            "tls_provider_library": "/provider.so",
            "tls_provider_library_sha256": "a" * 63,
        },
        {"tls_provider_max_bytes": 0},
        {"tls_provider_max_bytes": True},
    )
    for values in invalid:
        try:
            ListenerConfig(
                tls_provider="pcc-native-tls-v1",
                tls_config=object(),
                **values,
            )
        except ValueError:
            continue
        raise AssertionError("unsafe TLS provider provenance was accepted")
