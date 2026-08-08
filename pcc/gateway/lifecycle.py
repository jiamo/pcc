"""Gateway generations, admission limits, drain state and bounded counters."""

from threading import Lock

STATE_NEW = 0
STATE_STARTING = 1
STATE_RUNNING = 2
STATE_DRAINING = 3
STATE_STOPPED = 4
STATE_FAILED = 5


class GatewayGeneration:
    """One immutable generation.

    Standalone generations own a lock.  Generations installed in one
    ``GatewayLifecycle`` share its owner lock, eliminating nested lock waits.
    No resource callback runs while that lock is held.
    """

    def __init__(
        self,
        generation_id: int,
        config,
        resources=(),
        owner_lock=None,
    ) -> None:
        if owner_lock is None:
            owner_lock = Lock()
        self._lock = owner_lock
        self.generation_id = generation_id
        self.config = config
        self._resources = []
        self._references = 1
        self._retired = False
        self._released = False
        try:
            for resource in resources:
                self._resources.append(resource.retain())
        except Exception as error:
            # Construction is not published yet.  Undo every completed retain
            # without holding a gateway lock across resource callbacks.
            first_cleanup_error = None
            index = len(self._resources) - 1
            while index >= 0:
                try:
                    self._resources[index].release()
                except Exception as cleanup_error:
                    if first_cleanup_error is None:
                        first_cleanup_error = cleanup_error
                index -= 1
            self._resources = []
            if first_cleanup_error is not None:
                raise RuntimeError(
                    "gateway generation construction cleanup failed"
                ) from first_cleanup_error
            raise error

    @property
    def resources(self):
        self._lock.acquire()
        try:
            return list(self._resources)
        finally:
            self._lock.release()

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
    def released(self) -> bool:
        self._lock.acquire()
        try:
            return self._released
        finally:
            self._lock.release()

    def retain(self):
        self._lock.acquire()
        try:
            self._retain_locked()
        finally:
            self._lock.release()
        return self

    def _retain_locked(self) -> None:
        if self._released:
            raise RuntimeError("cannot retain released gateway generation")
        self._references += 1

    def attach_resource(self, resource) -> None:
        """Attach one retained resource before this generation is published."""

        self._lock.acquire()
        try:
            attachable = (
                not self._retired
                and not self._released
                and self._references == 1
            )
        finally:
            self._lock.release()
        if not attachable:
            raise RuntimeError("resources attach only to an unpublished generation")

        # retain/release are provider callbacks and may allocate, close native
        # state, or re-enter lifecycle inspection.  Never call them under the
        # generation lock.  Revalidate after the retain closes the race with a
        # concurrent acquire/retire.
        retained = resource.retain()
        attached = False
        self._lock.acquire()
        try:
            if (
                not self._retired
                and not self._released
                and self._references == 1
            ):
                self._resources.append(retained)
                attached = True
        finally:
            self._lock.release()
        if attached:
            return
        try:
            retained.release()
        except Exception as error:
            raise RuntimeError(
                "rejected gateway generation resource cleanup failed"
            ) from error
        raise RuntimeError("resources attach only to an unpublished generation")

    def retire(self) -> int:
        resources = None
        self._lock.acquire()
        try:
            if self._retired:
                raise RuntimeError("gateway generation retired more than once")
            if self._released or self._references <= 0:
                raise RuntimeError("gateway generation released more than once")
            self._retired = True
            self._references -= 1
            if self._references == 0:
                self._released = True
                resources = self._resources
                self._resources = []
        finally:
            self._lock.release()
        return self._release_resources(resources)

    def release(self) -> int:
        resources = None
        self._lock.acquire()
        try:
            if self._released or self._references <= 0:
                raise RuntimeError("gateway generation released more than once")
            self._references -= 1
            if self._references == 0:
                self._released = True
                resources = self._resources
                self._resources = []
        finally:
            self._lock.release()
        return self._release_resources(resources)

    def _release_resources(self, resources) -> int:
        if resources is None:
            return 0
        first_error = None
        index = len(resources) - 1
        while index >= 0:
            try:
                resources[index].release()
            except Exception as error:
                if first_error is None:
                    first_error = error
            index -= 1
        if first_error is not None:
            raise RuntimeError(
                "gateway generation resource release failed"
            ) from first_error
        return 1


class AdmissionLimits:
    def __init__(
        self,
        max_connections: int = 4096,
        max_requests: int = 4096,
        max_queued_requests: int = 1024,
        max_upstream_active: int = 2048,
        max_buffered_bytes: int = 67108864,
    ) -> None:
        values = (
            max_connections,
            max_requests,
            max_queued_requests,
            max_upstream_active,
            max_buffered_bytes,
        )
        for value in values:
            if value < 0:
                raise ValueError("admission limits cannot be negative")
        self.max_connections = max_connections
        self.max_requests = max_requests
        self.max_queued_requests = max_queued_requests
        self.max_upstream_active = max_upstream_active
        self.max_buffered_bytes = max_buffered_bytes


class GatewayMetrics:
    """Fixed-name integer counters; labels cannot grow at runtime."""

    NAMES = (
        "connections_accepted",
        "connections_active",
        "connections_rejected",
        "tls_handshakes_started",
        "tls_handshakes_completed",
        "tls_handshakes_failed",
        "tls_close_notify_completed",
        "tls_close_notify_failed",
        "tls_generation_reloads",
        "reload_requested",
        "requests_started",
        "requests_active",
        "requests_queued",
        "requests_rejected",
        "parser_errors",
        "buffered_bytes",
        "backpressure_parks",
        "upstream_active",
        "upstream_retries",
        "upstream_cancelled",
        "dns_queries",
        "dns_failures",
        "dns_cache_hits",
        "vthread_pins",
        "drain_forced",
    )

    def __init__(self) -> None:
        self._lock = Lock()
        self._values = {}
        for name in self.NAMES:
            self._values[name] = 0

    @property
    def values(self):
        """Compatibility snapshot; callers cannot mutate live counters."""

        return self.snapshot()

    def add(self, name: str, amount: int = 1) -> int:
        self._lock.acquire()
        try:
            return self._add_unlocked(name, amount)
        finally:
            self._lock.release()

    def get(self, name: str) -> int:
        self._lock.acquire()
        try:
            return self._get_unlocked(name)
        finally:
            self._lock.release()

    def snapshot(self):
        self._lock.acquire()
        try:
            return dict(self._values)
        finally:
            self._lock.release()

    def _add_unlocked(self, name: str, amount: int = 1) -> int:
        if name not in self._values:
            raise KeyError("unbounded gateway metric label rejected")
        self._values[name] += amount
        return self._values[name]

    def _get_unlocked(self, name: str) -> int:
        return self._values[name]

    def _drain_active_unlocked(self, force: bool) -> int:
        """Read, and when forced clear, both active gauges under owner lock."""

        active = (
            self._values["connections_active"]
            + self._values["requests_active"]
        )
        if active != 0 and force:
            self._values["drain_forced"] += active
            self._values["connections_active"] = 0
            self._values["requests_active"] = 0
        return active


class GatewayLifecycle:
    """Linearized gateway state for all carriers.

    ``GatewayServer.generation_lock`` may be the outer lock around reload and
    accept.  The order is server generation -> lifecycle owner.  Metrics and
    lifecycle-managed generations use that same owner lock, not nested locks.
    Lifecycle/generation code never acquires the server lock, so there is no
    reverse edge in this subsystem.
    """

    def __init__(self, config, limits=None) -> None:
        if limits is None:
            limits = AdmissionLimits()
        # Lifecycle state and all metrics share one lock.  This keeps each
        # admission check+counter update atomic without acquiring a second
        # potentially parking lock while the first is held.  Standalone metric
        # calls use the same lock through GatewayMetrics.add/get/snapshot.
        self._lock = Lock()
        self.metrics = GatewayMetrics()
        self.metrics._lock = self._lock
        self._state = STATE_NEW
        self._next_generation_id = 2
        self._current = GatewayGeneration(1, config, (), self._lock)
        self._retired_generations = []
        self._publishing = False
        self.limits = limits
        self._queued_requests = 0
        self._failed_reason = ""

    @property
    def state(self) -> int:
        self._lock.acquire()
        try:
            return self._state
        finally:
            self._lock.release()

    @property
    def current(self):
        self._lock.acquire()
        try:
            return self._current
        finally:
            self._lock.release()

    @property
    def queued_requests(self) -> int:
        self._lock.acquire()
        try:
            return self._queued_requests
        finally:
            self._lock.release()

    @property
    def failed_reason(self) -> str:
        self._lock.acquire()
        try:
            return self._failed_reason
        finally:
            self._lock.release()

    def start(self) -> None:
        self._lock.acquire()
        try:
            if self._state != STATE_NEW:
                raise RuntimeError("gateway can start only once")
            self._state = STATE_STARTING
        finally:
            self._lock.release()

    def started(self) -> None:
        self._lock.acquire()
        try:
            if self._state != STATE_STARTING:
                raise RuntimeError("gateway is not starting")
            self._state = STATE_RUNNING
        finally:
            self._lock.release()

    def acquire_generation(self):
        self._lock.acquire()
        try:
            if self._state != STATE_RUNNING:
                raise RuntimeError("gateway is not accepting new work")
            # Lifecycle-managed generations share this lock, so increment the
            # reference directly without recursively acquiring it.
            self._current._retain_locked()
            return self._current
        finally:
            self._lock.release()

    def publish(self, config, resources=()):
        self._lock.acquire()
        try:
            if self._state != STATE_RUNNING:
                raise RuntimeError("configuration publish requires running state")
            if self._publishing:
                raise RuntimeError("concurrent configuration publish rejected")
            self._publishing = True
            generation_id = self._next_generation_id
            self._next_generation_id += 1
        finally:
            self._lock.release()

        # Resource retain callbacks and generation construction stay outside
        # the lifecycle lock.  _publishing makes concurrent reload fail closed
        # instead of reordering generation ids.
        try:
            replacement = GatewayGeneration(
                generation_id,
                config,
                resources,
                self._lock,
            )
        except Exception:
            self._lock.acquire()
            try:
                self._publishing = False
            finally:
                self._lock.release()
            raise

        previous = None
        self._lock.acquire()
        try:
            if self._state == STATE_RUNNING:
                previous = self._current
                self._current = replacement
                self._retired_generations.append(previous)
            else:
                self._publishing = False
        finally:
            self._lock.release()

        if previous is None:
            # A concurrent drain/failure won while resources were retained.
            # Dispose the unpublished candidate without a lifecycle lock.
            replacement.retire()
            raise RuntimeError("configuration publish lost running state")

        # Provider/resource callbacks happen after the replacement is visible
        # and after the lifecycle lock has been released.
        try:
            previous.retire()
        finally:
            self._lock.acquire()
            try:
                self._publishing = False
            finally:
                self._lock.release()
            self.collect_retired()
        return replacement

    def collect_retired(self) -> int:
        self._lock.acquire()
        try:
            kept = []
            collected = 0
            for generation in self._retired_generations:
                # Lifecycle-managed generations share this owner lock.
                if generation._released:
                    collected += 1
                else:
                    kept.append(generation)
            self._retired_generations = kept
            return collected
        finally:
            self._lock.release()

    def admit_connection(self) -> bool:
        self._lock.acquire()
        try:
            if self._state != STATE_RUNNING:
                self.metrics._add_unlocked("connections_rejected")
                return False
            if self.metrics._get_unlocked("connections_active") >= self.limits.max_connections:
                self.metrics._add_unlocked("connections_rejected")
                return False
            self.metrics._add_unlocked("connections_accepted")
            self.metrics._add_unlocked("connections_active")
            return True
        finally:
            self._lock.release()

    def release_connection(self) -> None:
        self._lock.acquire()
        try:
            if self.metrics._get_unlocked("connections_active") <= 0:
                raise RuntimeError("connection admission released more than once")
            self.metrics._add_unlocked("connections_active", -1)
        finally:
            self._lock.release()

    def admit_request(self) -> bool:
        self._lock.acquire()
        try:
            if self._state != STATE_RUNNING:
                self.metrics._add_unlocked("requests_rejected")
                return False
            if self.metrics._get_unlocked("requests_active") >= self.limits.max_requests:
                self.metrics._add_unlocked("requests_rejected")
                return False
            self.metrics._add_unlocked("requests_started")
            self.metrics._add_unlocked("requests_active")
            return True
        finally:
            self._lock.release()

    def release_request(self) -> None:
        self._lock.acquire()
        try:
            if self.metrics._get_unlocked("requests_active") <= 0:
                raise RuntimeError("request admission released more than once")
            self.metrics._add_unlocked("requests_active", -1)
        finally:
            self._lock.release()

    def queue_request(self) -> bool:
        self._lock.acquire()
        try:
            if self._queued_requests >= self.limits.max_queued_requests:
                self.metrics._add_unlocked("requests_rejected")
                return False
            self._queued_requests += 1
            self.metrics._add_unlocked("requests_queued")
            return True
        finally:
            self._lock.release()

    def release_queued_request(self) -> None:
        self._lock.acquire()
        try:
            if self._queued_requests <= 0:
                raise RuntimeError("queued request released more than once")
            self._queued_requests -= 1
            self.metrics._add_unlocked("requests_queued", -1)
        finally:
            self._lock.release()

    def admit_upstream(self) -> bool:
        self._lock.acquire()
        try:
            if self.metrics._get_unlocked("upstream_active") >= self.limits.max_upstream_active:
                return False
            self.metrics._add_unlocked("upstream_active")
            return True
        finally:
            self._lock.release()

    def release_upstream(self) -> None:
        self._lock.acquire()
        try:
            if self.metrics._get_unlocked("upstream_active") <= 0:
                raise RuntimeError("upstream admission released more than once")
            self.metrics._add_unlocked("upstream_active", -1)
        finally:
            self._lock.release()

    def reserve_buffered(self, amount: int) -> bool:
        if amount < 0:
            raise ValueError("buffer reservation cannot be negative")
        self._lock.acquire()
        try:
            current = self.metrics._get_unlocked("buffered_bytes")
            if current + amount > self.limits.max_buffered_bytes:
                return False
            self.metrics._add_unlocked("buffered_bytes", amount)
            return True
        finally:
            self._lock.release()

    def release_buffered(self, amount: int) -> None:
        self._lock.acquire()
        try:
            if amount < 0 or amount > self.metrics._get_unlocked("buffered_bytes"):
                raise RuntimeError("buffer reservation released out of range")
            self.metrics._add_unlocked("buffered_bytes", -amount)
        finally:
            self._lock.release()

    def begin_drain(self) -> None:
        self._lock.acquire()
        try:
            if self._state not in (STATE_STARTING, STATE_RUNNING):
                raise RuntimeError("gateway cannot enter drain from this state")
            self._state = STATE_DRAINING
        finally:
            self._lock.release()

    def finish_drain(self, force: bool = False) -> bool:
        current = None
        self._lock.acquire()
        try:
            if self._state != STATE_DRAINING:
                raise RuntimeError("gateway is not draining")
            active = self.metrics._drain_active_unlocked(force)
            if active != 0 and not force:
                return False
            current = self._current
            self._state = STATE_STOPPED
        finally:
            self._lock.release()
        # Resource/provider release may call arbitrary native code.
        current.retire()
        return True

    def fail(self, reason: str) -> None:
        self._lock.acquire()
        try:
            self._failed_reason = reason
            self._state = STATE_FAILED
        finally:
            self._lock.release()
