"""Process-wide gateway control ABI and deterministic host-model contracts."""

from pathlib import Path
import ast

import pytest

from pcc.gateway.control import (
    DISPOSITION_DRAIN,
    DISPOSITION_IGNORE,
    DISPOSITION_RELOAD,
    GatewayControlError,
    GatewayControlHostModel,
    GatewayControlPoll,
    GatewayProcessControl,
    PCC_GATEWAY_CONTROL_ABI_NAME,
    PCC_GATEWAY_CONTROL_ERROR_BUSY,
    PCC_GATEWAY_CONTROL_ERROR_CONTRACT,
    PCC_GATEWAY_CONTROL_ERROR_NOT_OWNER,
    PCC_GATEWAY_CONTROL_RELOAD,
    PCC_GATEWAY_CONTROL_STOP,
    PccNativeGatewayControlAbi,
    SIGHUP,
    SIGINT,
    SIGPIPE,
    SIGTERM,
)
from pcc.py_frontend.pipeline_freestanding import (
    freestanding_allowed_external_symbols,
)


REPO = Path(__file__).resolve().parents[2]


def test_native_wrapper_names_fixed_no_libpython_control_symbols() -> None:
    native = PccNativeGatewayControlAbi()
    assert native.abi_name == "pcc-native-gateway-control-v1"
    assert native.abi_name == PCC_GATEWAY_CONTROL_ABI_NAME
    assert native.abi_version == 1
    assert native.production_ready
    assert "no-libpython" in native.link_boundary

    source = (REPO / "pcc" / "gateway" / "control.py").read_text(
        encoding="utf-8"
    )
    assert 'extern("pcc_gateway_control_v1_install"' in source
    assert '"pcc_gateway_control_v1_poll"' in source
    assert '"pcc_gateway_control_v1_uninstall"' in source
    assert "from .server" not in source
    assert "import signal" not in source
    assert "signal.signal" not in source
    assert "libpython" in source


def test_freestanding_control_has_no_executable_constant_assignments() -> None:
    source_path = (
        REPO / "pcc" / "py_runtime" / "py" / "freestanding_gateway_control.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    assignments = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]

    assert len(assignments) == 3
    for assignment in assignments:
        assert isinstance(assignment, ast.Assign)
        assert isinstance(assignment.targets[0], ast.Name)
        if assignment.targets[0].id == "__pcc_freestanding__":
            assert isinstance(assignment.value, ast.Constant)
            assert assignment.value.value is True
            continue
        assert isinstance(assignment.value, ast.Call)
        assert isinstance(assignment.value.func, ast.Name)
        assert assignment.value.func.id == "extern"

    allowed = freestanding_allowed_external_symbols(
        source_path.read_text(encoding="utf-8")
    )
    assert {"sigaction", "sigemptyset", "memset"} <= allowed

    wrong_signature = source_path.read_text(encoding="utf-8").replace(
        'extern("sigaction", (c_int, c_ptr, c_ptr), c_int)',
        'extern("sigaction", (c_ptr, c_ptr, c_ptr), c_int)',
    )
    assert "sigaction" not in freestanding_allowed_external_symbols(
        wrong_signature
    )


def test_freestanding_restore_failures_keep_owner_and_pending_flags() -> None:
    source = (
        REPO / "pcc" / "py_runtime" / "py" / "freestanding_gateway_control.py"
    ).read_text(encoding="utf-8")

    assert 'define_global_i32("pcc_gateway_control_action_mask", 0)' in source
    assert "def _restore_action(signal_number: i64, old_action) -> i64:" in source
    assert "return _sigaction(signal_number, old_action, null())" in source
    assert "def _restore_modified_actions() -> i64:" in source
    assert "if _restore_modified_actions() != 0:" in source
    assert 'atomic_store_i32(installed, 0, 2, "release")' in source
    assert "return -4" in source

    poll_start = source.index("def pcc_gateway_control_v1_poll")
    uninstall_start = source.index("def pcc_gateway_control_v1_uninstall")
    poll_source = source[poll_start:uninstall_start]
    assert "pcc_gateway_control_installed" in poll_source
    assert "!= 1" in poll_source
    assert poll_source.index("!= 1") < poll_source.index(
        'global_addr("pcc_gateway_control_stop_requested")'
    )

    uninstall_source = source[uninstall_start:]
    restore_failure = uninstall_source.index(
        "if _restore_modified_actions() != 0:"
    )
    clear_stop = uninstall_source.index(
        'global_addr("pcc_gateway_control_stop_requested")'
    )
    release_owner = uninstall_source.index(
        'atomic_store_i32(installed, 0, 0, "release")'
    )
    assert restore_failure < clear_stop < release_owner


def test_partial_install_rollback_is_retryable_and_never_overwrites_saved_actions() -> None:
    source = (
        REPO / "pcc" / "py_runtime" / "py" / "freestanding_gateway_control.py"
    ).read_text(encoding="utf-8")
    install_start = source.index("def pcc_gateway_control_v1_install")
    poll_start = source.index("def pcc_gateway_control_v1_poll")
    install_source = source[install_start:poll_start]

    recovery_claim = install_source.index(
        'installed, 0, 3, 4, "acq_rel", "acquire"'
    )
    recovery_restore = install_source.index("_restore_modified_actions()")
    first_new_action = install_source.index("_set_action(13")
    assert recovery_claim < recovery_restore < first_new_action
    assert install_source.count("return _failed_install()") == 4
    assert 'atomic_store_i32(action_mask, 0, 15, "release")' in install_source
    assert 'atomic_store_i32(installed, 0, 1, "release")' in install_source

    recovery_restore = install_source.index("_restore_modified_actions()")
    clear_stop = install_source.index(
        'global_addr("pcc_gateway_control_stop_requested")',
        recovery_restore,
    )
    clear_reload = install_source.index(
        'global_addr("pcc_gateway_control_reload_requested")',
        recovery_restore,
    )
    first_new_action = install_source.index("_set_action(13")
    assert recovery_restore < clear_stop < first_new_action
    assert recovery_restore < clear_reload < first_new_action


def test_signal_mask_boundary_is_explicit_and_does_not_overclaim_thread_safety() -> None:
    source = (
        REPO / "pcc" / "py_runtime" / "py" / "freestanding_gateway_control.py"
    ).read_text(encoding="utf-8")

    assert "does not currently admit ``sigprocmask``" in source
    assert "install before carrier threads" in source
    assert "only after they have quiesced" in source
    assert 'extern("sigprocmask"' not in source
    assert 'extern("pthread_sigmask"' not in source


def test_host_model_installs_exact_dispositions_and_restores_previous() -> None:
    previous = {
        SIGHUP: "old-hup",
        SIGINT: "old-int",
        SIGPIPE: "old-pipe",
        SIGTERM: "old-term",
    }
    model = GatewayControlHostModel(previous)
    control = GatewayProcessControl(model)
    assert not model.production_ready

    control.install()
    assert model.disposition(SIGTERM) == DISPOSITION_DRAIN
    assert model.disposition(SIGINT) == DISPOSITION_DRAIN
    assert model.disposition(SIGHUP) == DISPOSITION_RELOAD
    assert model.disposition(SIGPIPE) == DISPOSITION_IGNORE

    control.uninstall()
    assert model.disposition(SIGHUP) == "old-hup"
    assert model.disposition(SIGINT) == "old-int"
    assert model.disposition(SIGPIPE) == "old-pipe"
    assert model.disposition(SIGTERM) == "old-term"


def test_poll_consumes_coalesced_drain_and_reload_flags() -> None:
    model = GatewayControlHostModel()
    control = GatewayProcessControl(model)
    control.install()

    assert model.deliver(SIGTERM)
    assert model.deliver(SIGINT)
    assert model.deliver(SIGTERM)
    assert model.deliver(SIGHUP)
    assert model.deliver(SIGHUP)
    assert model.deliver(SIGPIPE)
    assert not model.deliver(9)
    first = control.poll()
    assert first.flags == PCC_GATEWAY_CONTROL_STOP | PCC_GATEWAY_CONTROL_RELOAD
    assert first.stop_requested
    assert first.reload_requested
    assert not first.empty

    second = control.poll()
    assert second.empty
    assert not second.stop_requested
    assert not second.reload_requested
    control.uninstall()
    assert not model.deliver(SIGTERM)


def test_process_owner_conflict_and_wrong_capability_fail_closed() -> None:
    model = GatewayControlHostModel()
    first = GatewayProcessControl(model)
    second = GatewayProcessControl(model)
    first.install()

    with pytest.raises(GatewayControlError) as busy:
        second.install()
    assert busy.value.error_code == PCC_GATEWAY_CONTROL_ERROR_BUSY
    assert model.poll(first.owner + 1) == PCC_GATEWAY_CONTROL_ERROR_NOT_OWNER
    assert model.uninstall(first.owner + 1) == PCC_GATEWAY_CONTROL_ERROR_NOT_OWNER

    first.uninstall()
    second.install()
    assert second.owner != 0
    second.uninstall()


def test_wrapper_rejects_double_or_unowned_operations() -> None:
    model = GatewayControlHostModel()
    control = GatewayProcessControl(model)
    with pytest.raises(GatewayControlError, match="poll-state"):
        control.poll()
    with pytest.raises(GatewayControlError, match="uninstall-state"):
        control.uninstall()

    control.install()
    with pytest.raises(GatewayControlError, match="install-state"):
        control.install()
    control.uninstall()
    with pytest.raises(GatewayControlError, match="uninstall-state"):
        control.uninstall()


class ScriptedAbi:
    abi_version = 1
    abi_name = PCC_GATEWAY_CONTROL_ABI_NAME
    production_ready = False
    link_boundary = "scripted:test-only"

    def __init__(self) -> None:
        self.install_result = 7
        self.poll_result = 0
        self.uninstall_result = 0

    def install(self):
        return self.install_result

    def poll(self, owner):
        return self.poll_result

    def uninstall(self, owner):
        return self.uninstall_result


def test_unknown_flags_and_failed_uninstall_retain_ownership() -> None:
    abi = ScriptedAbi()
    control = GatewayProcessControl(abi)
    control.install()

    abi.poll_result = 4
    with pytest.raises(GatewayControlError) as unknown:
        control.poll()
    assert unknown.value.error_code == PCC_GATEWAY_CONTROL_ERROR_CONTRACT

    abi.uninstall_result = -71
    with pytest.raises(GatewayControlError) as failed:
        control.uninstall()
    assert failed.value.error_code == -71
    assert control.installed
    assert control.owner == 7

    abi.uninstall_result = 0
    control.uninstall()
    assert not control.installed
    assert control.owner == 0


def test_abi_metadata_and_poll_result_shape_are_fail_closed() -> None:
    abi = ScriptedAbi()
    abi.abi_version = 2
    with pytest.raises(GatewayControlError, match="abi-version"):
        GatewayProcessControl(abi)

    event = GatewayControlPoll(PCC_GATEWAY_CONTROL_RELOAD)
    assert event.reload_requested and not event.stop_requested
    with pytest.raises(GatewayControlError, match="poll-contract"):
        GatewayControlPoll(8)


def test_missing_native_abi_never_falls_back_to_python_signal() -> None:
    control = GatewayProcessControl()
    with pytest.raises(GatewayControlError, match="install") as failed:
        control.install()
    assert failed.value.operation == "install"
    assert not control.installed
    assert control.owner == 0
