"""Process-wide signal ownership for the native gateway.

The gateway loop polls two allocation-free notification bits.  Signal handlers
themselves belong below this module, behind a fixed native ABI: SIGTERM and
SIGINT set the stop bit, SIGHUP sets the reload bit, and SIGPIPE is ignored.
The native implementation must save all four previous dispositions and restore
them atomically on uninstall.  It must not call Python, allocate, schedule, or
perform gateway lifecycle work from a signal handler.

This source intentionally does not fall back to :mod:`signal`.  The production
C ABI is a required link boundary for current-pcc1.  Until that ABI is linked,
``GatewayProcessControl.install()`` fails closed.  ``GatewayControlHostModel``
is a deterministic test oracle only; it is never selected implicitly.
"""

from pcc.extern import c_int64, extern


PCC_GATEWAY_CONTROL_ABI_VERSION = 1
PCC_GATEWAY_CONTROL_ABI_NAME = "pcc-native-gateway-control-v1"

PCC_GATEWAY_CONTROL_STOP = 1
PCC_GATEWAY_CONTROL_RELOAD = 2
PCC_GATEWAY_CONTROL_KNOWN_FLAGS = (
    PCC_GATEWAY_CONTROL_STOP | PCC_GATEWAY_CONTROL_RELOAD
)

PCC_GATEWAY_CONTROL_ERROR_BUSY = -1
PCC_GATEWAY_CONTROL_ERROR_NOT_OWNER = -2
PCC_GATEWAY_CONTROL_ERROR_PLATFORM = -3
PCC_GATEWAY_CONTROL_ERROR_CONTRACT = -4

SIGHUP = 1
SIGINT = 2
SIGPIPE = 13
SIGTERM = 15

DISPOSITION_DEFAULT = "default"
DISPOSITION_DRAIN = "drain"
DISPOSITION_RELOAD = "reload"
DISPOSITION_IGNORE = "ignore"


_native_install = extern("pcc_gateway_control_v1_install", (), c_int64)
_native_poll = extern(
    "pcc_gateway_control_v1_poll", (c_int64,), c_int64
)
_native_uninstall = extern(
    "pcc_gateway_control_v1_uninstall", (c_int64,), c_int64
)


class GatewayControlError(RuntimeError):
    """Stable process-control ownership or native-ABI failure."""

    def __init__(self, operation: str, error_code: int) -> None:
        self.operation = operation
        self.error_code = error_code
        super().__init__(
            "gateway process control " + operation + " failed ("
            + str(error_code) + ")"
        )


class GatewayControlPoll:
    """One consumed snapshot of coalesced process notifications."""

    def __init__(self, flags: int = 0) -> None:
        if flags < 0 or flags & ~PCC_GATEWAY_CONTROL_KNOWN_FLAGS:
            raise GatewayControlError(
                "poll-contract", PCC_GATEWAY_CONTROL_ERROR_CONTRACT
            )
        self.flags = flags
        self.stop_requested = bool(flags & PCC_GATEWAY_CONTROL_STOP)
        self.reload_requested = bool(flags & PCC_GATEWAY_CONTROL_RELOAD)

    @property
    def empty(self) -> bool:
        return self.flags == 0


class PccNativeGatewayControlAbi:
    """Direct no-libpython adapter for the production control ABI."""

    abi_version = PCC_GATEWAY_CONTROL_ABI_VERSION
    abi_name = PCC_GATEWAY_CONTROL_ABI_NAME
    production_ready = True
    link_boundary = (
        "direct-symbols:pcc_gateway_control_v1_install,poll,uninstall;"
        "no-libpython;no-python-signal-handler"
    )

    def install(self) -> int:
        return _native_install()

    def poll(self, owner: int) -> int:
        return _native_poll(owner)

    def uninstall(self, owner: int) -> int:
        return _native_uninstall(owner)


class GatewayProcessControl:
    """Exactly-once owner of the process-wide native signal installation.

    ``install`` returns no public token; the native owner capability remains
    private to this wrapper.  Poll consumes coalesced flags.  An unsuccessful
    uninstall deliberately retains the capability because restoring the prior
    dispositions has not been proven.
    """

    def __init__(self, abi=None) -> None:
        if abi is None:
            abi = PccNativeGatewayControlAbi()
        if getattr(abi, "abi_version", 0) != PCC_GATEWAY_CONTROL_ABI_VERSION:
            raise GatewayControlError(
                "abi-version", PCC_GATEWAY_CONTROL_ERROR_CONTRACT
            )
        if getattr(abi, "abi_name", "") != PCC_GATEWAY_CONTROL_ABI_NAME:
            raise GatewayControlError(
                "abi-name", PCC_GATEWAY_CONTROL_ERROR_CONTRACT
            )
        if not isinstance(getattr(abi, "production_ready", None), bool):
            raise GatewayControlError(
                "abi-provenance", PCC_GATEWAY_CONTROL_ERROR_CONTRACT
            )
        if not getattr(abi, "link_boundary", ""):
            raise GatewayControlError(
                "abi-provenance", PCC_GATEWAY_CONTROL_ERROR_CONTRACT
            )
        for method_name in ("install", "poll", "uninstall"):
            if not callable(getattr(abi, method_name, None)):
                raise GatewayControlError(
                    "abi-method", PCC_GATEWAY_CONTROL_ERROR_CONTRACT
                )
        self.abi = abi
        self.owner = 0
        self.installed = False

    def install(self) -> None:
        if self.installed or self.owner != 0:
            raise GatewayControlError(
                "install-state", PCC_GATEWAY_CONTROL_ERROR_CONTRACT
            )
        try:
            owner = self.abi.install()
        except Exception as error:
            raise GatewayControlError(
                "install", PCC_GATEWAY_CONTROL_ERROR_PLATFORM
            ) from error
        if not isinstance(owner, int) or owner <= 0:
            error_code = owner if isinstance(owner, int) else (
                PCC_GATEWAY_CONTROL_ERROR_CONTRACT
            )
            raise GatewayControlError("install", error_code)
        self.owner = owner
        self.installed = True

    def poll(self) -> GatewayControlPoll:
        if not self.installed or self.owner <= 0:
            raise GatewayControlError(
                "poll-state", PCC_GATEWAY_CONTROL_ERROR_NOT_OWNER
            )
        try:
            flags = self.abi.poll(self.owner)
        except Exception as error:
            raise GatewayControlError(
                "poll", PCC_GATEWAY_CONTROL_ERROR_PLATFORM
            ) from error
        if not isinstance(flags, int):
            raise GatewayControlError(
                "poll-contract", PCC_GATEWAY_CONTROL_ERROR_CONTRACT
            )
        if flags < 0:
            raise GatewayControlError("poll", flags)
        return GatewayControlPoll(flags)

    def uninstall(self) -> None:
        if not self.installed or self.owner <= 0:
            raise GatewayControlError(
                "uninstall-state", PCC_GATEWAY_CONTROL_ERROR_NOT_OWNER
            )
        owner = self.owner
        try:
            status = self.abi.uninstall(owner)
        except Exception as error:
            raise GatewayControlError(
                "uninstall", PCC_GATEWAY_CONTROL_ERROR_PLATFORM
            ) from error
        if not isinstance(status, int):
            raise GatewayControlError(
                "uninstall-contract", PCC_GATEWAY_CONTROL_ERROR_CONTRACT
            )
        if status != 0:
            raise GatewayControlError("uninstall", status)
        self.owner = 0
        self.installed = False


class GatewayControlHostModel:
    """Single-process deterministic ABI oracle for source tests.

    Sharing one model between wrappers models one OS process.  ``deliver`` is
    the test-only stand-in for an async signal; drain/reload notifications
    coalesce until the owner polls them.  It performs no real signal mutation.
    """

    abi_version = PCC_GATEWAY_CONTROL_ABI_VERSION
    abi_name = PCC_GATEWAY_CONTROL_ABI_NAME
    production_ready = False
    link_boundary = "python-host-model:test-only;no-real-signal-install"

    def __init__(self, previous_dispositions=None) -> None:
        if previous_dispositions is None:
            previous_dispositions = {}
        self.dispositions = {
            SIGHUP: previous_dispositions.get(SIGHUP, DISPOSITION_DEFAULT),
            SIGINT: previous_dispositions.get(SIGINT, DISPOSITION_DEFAULT),
            SIGPIPE: previous_dispositions.get(SIGPIPE, DISPOSITION_DEFAULT),
            SIGTERM: previous_dispositions.get(SIGTERM, DISPOSITION_DEFAULT),
        }
        self.saved_dispositions = None
        self.owner = 0
        self.next_owner = 1
        self.pending_flags = 0

    def install(self) -> int:
        if self.owner != 0:
            return PCC_GATEWAY_CONTROL_ERROR_BUSY
        owner = self.next_owner
        self.next_owner += 1
        self.saved_dispositions = dict(self.dispositions)
        self.dispositions[SIGTERM] = DISPOSITION_DRAIN
        self.dispositions[SIGINT] = DISPOSITION_DRAIN
        self.dispositions[SIGHUP] = DISPOSITION_RELOAD
        self.dispositions[SIGPIPE] = DISPOSITION_IGNORE
        self.pending_flags = 0
        self.owner = owner
        return owner

    def poll(self, owner: int) -> int:
        if owner <= 0 or owner != self.owner:
            return PCC_GATEWAY_CONTROL_ERROR_NOT_OWNER
        flags = self.pending_flags
        self.pending_flags = 0
        return flags

    def uninstall(self, owner: int) -> int:
        if owner <= 0 or owner != self.owner:
            return PCC_GATEWAY_CONTROL_ERROR_NOT_OWNER
        if self.saved_dispositions is None:
            return PCC_GATEWAY_CONTROL_ERROR_CONTRACT
        self.dispositions = self.saved_dispositions
        self.saved_dispositions = None
        self.pending_flags = 0
        self.owner = 0
        return 0

    def deliver(self, signal_number: int) -> bool:
        if self.owner == 0:
            return False
        if signal_number == SIGTERM or signal_number == SIGINT:
            self.pending_flags |= PCC_GATEWAY_CONTROL_STOP
            return True
        if signal_number == SIGHUP:
            self.pending_flags |= PCC_GATEWAY_CONTROL_RELOAD
            return True
        if signal_number == SIGPIPE:
            return True
        return False

    def disposition(self, signal_number: int) -> str:
        return self.dispositions.get(signal_number, DISPOSITION_DEFAULT)


__all__ = [
    "PCC_GATEWAY_CONTROL_ABI_VERSION",
    "PCC_GATEWAY_CONTROL_ABI_NAME",
    "PCC_GATEWAY_CONTROL_STOP",
    "PCC_GATEWAY_CONTROL_RELOAD",
    "PCC_GATEWAY_CONTROL_KNOWN_FLAGS",
    "PCC_GATEWAY_CONTROL_ERROR_BUSY",
    "PCC_GATEWAY_CONTROL_ERROR_NOT_OWNER",
    "PCC_GATEWAY_CONTROL_ERROR_PLATFORM",
    "PCC_GATEWAY_CONTROL_ERROR_CONTRACT",
    "SIGHUP",
    "SIGINT",
    "SIGPIPE",
    "SIGTERM",
    "DISPOSITION_DEFAULT",
    "DISPOSITION_DRAIN",
    "DISPOSITION_RELOAD",
    "DISPOSITION_IGNORE",
    "GatewayControlError",
    "GatewayControlPoll",
    "PccNativeGatewayControlAbi",
    "GatewayProcessControl",
    "GatewayControlHostModel",
]
