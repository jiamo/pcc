"""Gateway immutable-generation and graceful-drain source contract."""

from pathlib import Path
from threading import Barrier, Lock, Thread

from pcc.gateway.lifecycle import (
    AdmissionLimits,
    GatewayLifecycle,
    GatewayMetrics,
    STATE_DRAINING,
    STATE_FAILED,
    STATE_RUNNING,
    STATE_STOPPED,
)


REPO = Path(__file__).resolve().parents[2]


def _running(config=None, limits=None):
    lifecycle = GatewayLifecycle(config or {}, limits)
    lifecycle.start()
    lifecycle.started()
    return lifecycle


def test_reload_publishes_before_retiring_old_generation() -> None:
    lifecycle = _running({"route": "old"})
    old = lifecycle.acquire_generation()
    new = lifecycle.publish({"route": "new"})
    assert lifecycle.current is new
    assert lifecycle.current.config == {"route": "new"}
    assert old.retired
    assert not old.released
    old.release()
    assert lifecycle.collect_retired() == 1


class _RetainedResource:
    def __init__(self) -> None:
        self.references = 1

    def retain(self):
        self.references += 1
        return self

    def release(self) -> None:
        self.references -= 1


def test_gateway_generation_pins_tls_generation_until_connections_drain() -> None:
    first_resource = _RetainedResource()
    lifecycle = _running({"certificate": "old"})
    lifecycle.current.attach_resource(first_resource)
    old = lifecycle.acquire_generation()
    second_resource = _RetainedResource()
    new = lifecycle.publish(
        {"certificate": "new"}, resources=(second_resource,)
    )
    assert new.resources == [second_resource]
    assert first_resource.references == 2
    assert second_resource.references == 2
    old.release()
    assert lifecycle.collect_retired() == 1
    assert first_resource.references == 1


def test_drain_stops_admission_before_active_scope_completion() -> None:
    lifecycle = _running()
    assert lifecycle.admit_connection()
    assert lifecycle.admit_request()
    lifecycle.begin_drain()
    assert lifecycle.state == STATE_DRAINING
    assert not lifecycle.admit_connection()
    assert not lifecycle.admit_request()
    assert not lifecycle.finish_drain()
    lifecycle.release_request()
    lifecycle.release_connection()
    assert lifecycle.finish_drain()
    assert lifecycle.state == STATE_STOPPED


def test_forced_drain_is_named_and_idempotent_release_is_enforced() -> None:
    lifecycle = _running()
    assert lifecycle.admit_connection()
    assert lifecycle.admit_request()
    lifecycle.begin_drain()
    assert lifecycle.finish_drain(force=True)
    assert lifecycle.metrics.get("drain_forced") == 2
    assert lifecycle.metrics.get("connections_active") == 0
    assert lifecycle.metrics.get("requests_active") == 0


def test_connection_and_request_limits_are_independent() -> None:
    lifecycle = _running(
        limits=AdmissionLimits(max_connections=1, max_requests=2)
    )
    assert lifecycle.admit_connection()
    assert not lifecycle.admit_connection()
    assert lifecycle.admit_request()
    assert lifecycle.admit_request()
    assert not lifecycle.admit_request()
    assert lifecycle.metrics.get("connections_rejected") == 1
    assert lifecycle.metrics.get("requests_rejected") == 1


def test_upstream_and_global_buffer_admission_release_exactly_once() -> None:
    lifecycle = _running(
        limits=AdmissionLimits(
            max_upstream_active=1,
            max_buffered_bytes=8,
        )
    )
    assert lifecycle.admit_upstream()
    assert not lifecycle.admit_upstream()
    lifecycle.release_upstream()
    assert lifecycle.reserve_buffered(5)
    assert not lifecycle.reserve_buffered(4)
    lifecycle.release_buffered(5)
    assert lifecycle.metrics.get("upstream_active") == 0
    assert lifecycle.metrics.get("buffered_bytes") == 0


def test_pipelined_queue_admission_is_bounded_and_released() -> None:
    lifecycle = _running(
        limits=AdmissionLimits(max_queued_requests=1)
    )
    assert lifecycle.queue_request()
    assert not lifecycle.queue_request()
    lifecycle.release_queued_request()
    assert lifecycle.queued_requests == 0
    assert lifecycle.metrics.get("requests_queued") == 0


def test_failed_startup_has_explicit_terminal_state() -> None:
    lifecycle = GatewayLifecycle({})
    lifecycle.start()
    lifecycle.fail("bind failed")
    assert lifecycle.state == STATE_FAILED
    assert lifecycle.failed_reason == "bind failed"
    assert not lifecycle.admit_connection()


def test_carriers_serialize_metrics_admission_and_queue_bounds() -> None:
    lifecycle = _running(
        limits=AdmissionLimits(
            max_connections=7,
            max_queued_requests=5,
        )
    )
    worker_count = 32
    start = Barrier(worker_count)
    result_lock = Lock()
    admitted = []
    queued = []

    def contend() -> None:
        start.wait()
        connection = lifecycle.admit_connection()
        queue = lifecycle.queue_request()
        with result_lock:
            admitted.append(connection)
            queued.append(queue)

    workers = [Thread(target=contend) for _ in range(worker_count)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert sum(1 for value in admitted if value) == 7
    assert sum(1 for value in queued if value) == 5
    assert lifecycle.metrics.get("connections_active") == 7
    assert lifecycle.queued_requests == 5
    assert lifecycle.metrics.get("requests_queued") == 5

    for _ in range(7):
        lifecycle.release_connection()
    for _ in range(5):
        lifecycle.release_queued_request()
    assert lifecycle.metrics.get("connections_active") == 0
    assert lifecycle.queued_requests == 0


def test_metric_add_has_no_lost_updates_across_carriers() -> None:
    metrics = GatewayMetrics()
    worker_count = 8
    iterations = 2000
    start = Barrier(worker_count)

    def add_many() -> None:
        start.wait()
        for _ in range(iterations):
            metrics.add("parser_errors")

    workers = [Thread(target=add_many) for _ in range(worker_count)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert metrics.get("parser_errors") == worker_count * iterations


class _ConcurrentRetainedResource:
    def __init__(self) -> None:
        self._lock = Lock()
        self.references = 1
        self.release_calls = 0

    def retain(self):
        with self._lock:
            self.references += 1
        return self

    def release(self) -> None:
        with self._lock:
            self.references -= 1
            self.release_calls += 1


def test_generation_retire_and_carrier_releases_drop_resources_once() -> None:
    lifecycle = _running({"route": "old"})
    resource = _ConcurrentRetainedResource()
    lifecycle.current.attach_resource(resource)
    old = lifecycle.current
    worker_count = 16
    retained = Barrier(worker_count + 1)
    release = Barrier(worker_count + 1)

    def hold_generation() -> None:
        generation = old.retain()
        retained.wait()
        release.wait()
        generation.release()

    workers = [Thread(target=hold_generation) for _ in range(worker_count)]
    for worker in workers:
        worker.start()
    retained.wait()
    lifecycle.publish({"route": "new"})
    assert old.retired and not old.released
    release.wait()
    for worker in workers:
        worker.join()

    assert old.released
    assert lifecycle.collect_retired() == 1
    assert resource.references == 1
    assert resource.release_calls == 1


def test_generation_resource_callbacks_can_reenter_lifecycle_without_deadlock() -> None:
    lifecycle = _running({"route": "old"})
    observed_states = []
    errors = []

    class ReentrantResource:
        def __init__(self) -> None:
            self.references = 1

        def retain(self):
            observed_states.append(lifecycle.state)
            self.references += 1
            return self

        def release(self) -> None:
            observed_states.append(lifecycle.state)
            self.references -= 1

    resource = ReentrantResource()

    def publish_and_drain() -> None:
        try:
            lifecycle.publish({"route": "new"}, resources=(resource,))
            lifecycle.begin_drain()
            assert lifecycle.finish_drain()
        except Exception as error:
            errors.append(error)

    worker = Thread(target=publish_and_drain, daemon=True)
    worker.start()
    worker.join(2)
    assert not worker.is_alive(), "resource callback ran while lifecycle lock was held"
    assert not errors
    assert observed_states == [STATE_RUNNING, STATE_STOPPED]
    assert resource.references == 1


def test_lifecycle_source_uses_native_locks_and_keeps_callbacks_outside() -> None:
    source = (REPO / "pcc" / "gateway" / "lifecycle.py").read_text(
        encoding="utf-8"
    )

    assert "from threading import Lock" in source
    assert "self.metrics._lock = self._lock" in source
    assert "self._current._retain_locked()" in source
    assert "resources,\n                self._lock," in source
    assert "def _add_unlocked" in source
    assert "concurrent configuration publish rejected" in source
    assert "Never call them under the" in source
    assert "after the lifecycle lock has been released" in source
    assert "current.retire()\n        return True" in source
