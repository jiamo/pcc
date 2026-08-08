"""Bounded gateway metric and overload observability contract."""

from pcc.gateway.lifecycle import GatewayMetrics


def test_metrics_have_fixed_cardinality_and_integer_snapshot() -> None:
    metrics = GatewayMetrics()
    metrics.add("connections_accepted")
    metrics.add("buffered_bytes", 4096)
    metrics.add("backpressure_parks", 2)
    snapshot = metrics.snapshot()
    assert set(snapshot) == set(GatewayMetrics.NAMES)
    assert snapshot["connections_accepted"] == 1
    assert snapshot["buffered_bytes"] == 4096
    assert snapshot["backpressure_parks"] == 2
    assert all(isinstance(value, int) for value in snapshot.values())


def test_arbitrary_runtime_labels_are_rejected() -> None:
    metrics = GatewayMetrics()
    try:
        metrics.add("route:/untrusted/user/input")
    except KeyError:
        return
    raise AssertionError("unbounded metric label was admitted")


def test_counter_decrement_is_explicit_for_live_gauges() -> None:
    metrics = GatewayMetrics()
    metrics.add("requests_active", 3)
    metrics.add("requests_active", -1)
    metrics.add("upstream_active", 2)
    metrics.add("upstream_active", -2)
    assert metrics.get("requests_active") == 2
    assert metrics.get("upstream_active") == 0
