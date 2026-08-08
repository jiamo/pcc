"""Focused contracts for the gateway's nonblocking socket boundary.

These tests intentionally stop below virtual-thread parking.  They establish
the syscall observation ABI that a current-pcc1 scheduler will consume: owned
fds stay nonblocking, buffers belong to the caller, and progress/control/error
outcomes are distinct.
"""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import textwrap

import pytest

from pcc.py_frontend import pipeline
from pcc1_gate import find_current_pcc1


REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "pcc" / "py_runtime" / "py" / "freestanding_platform_socket.py"


VIRTUAL_THREAD_TCP_ECHO_SOURCE = textwrap.dedent(
    '''
    from pcc.gateway import NativeSocketTransport, UpstreamEndpoint
    import pcc.virtual_thread as vt


    PORT = __PORT__
    MESSAGE = b"pcc1-tcp-echo"
    PROGRESS = 0
    WOULD_BLOCK = 1
    EOF = 2
    CONNECTED = 3


    def send_all(transport, fd: int, data: bytes) -> int:
        offset = 0
        while offset < len(data):
            outcome, count = transport.write(fd, data[offset:])
            if outcome == WOULD_BLOCK:
                vt.writable(fd)
            elif outcome == -4:
                pass
            elif outcome != PROGRESS or count <= 0:
                return 1
            else:
                offset = offset + count
        return 0


    def recv_exact(transport, fd: int, size: int):
        data = b""
        while len(data) < size:
            outcome, chunk = transport.read(fd, size - len(data))
            if outcome == WOULD_BLOCK:
                vt.readable(fd)
            elif outcome == -4:
                pass
            elif outcome == EOF:
                return b""
            elif outcome != PROGRESS or len(chunk) == 0:
                return b""
            else:
                data = data + chunk
        return data


    def echo_server(transport, listener: int) -> int:
        peer = -1
        client_ip = ""
        while peer < 0:
            outcome, accepted, observed_ip = transport.accept(listener)
            if outcome == WOULD_BLOCK:
                vt.readable(listener)
            elif outcome != PROGRESS or accepted < 0:
                return 10
            else:
                peer = accepted
                client_ip = observed_ip
        if client_ip != "127.0.0.1":
            transport.close(peer)
            return 11
        payload = recv_exact(transport, peer, len(MESSAGE))
        if payload != MESSAGE:
            transport.close(peer)
            return 12
        if send_all(transport, peer, payload) != 0:
            transport.close(peer)
            return 13
        transport.shutdown(peer)
        transport.close(peer)
        return len(payload)


    def echo_client(transport) -> int:
        endpoint = UpstreamEndpoint("127.0.0.1", PORT)
        fd = transport.open_upstream(endpoint, "127.0.0.1")
        if fd < 0:
            return 20
        connected = False
        while not connected:
            outcome = transport.connect_observe(fd)
            if outcome == CONNECTED:
                connected = True
            elif outcome == WOULD_BLOCK:
                vt.writable(fd)
            elif outcome == -4:
                pass
            else:
                transport.close(fd)
                return 21
        # Keep the accepted connection idle while another runnable task gets
        # carrier time; the server must be parked in the waitset, not spinning.
        vt.sleep_current(5)
        if send_all(transport, fd, MESSAGE) != 0:
            transport.close(fd)
            return 22
        echoed = recv_exact(transport, fd, len(MESSAGE))
        transport.close(fd)
        if echoed != MESSAGE:
            return 23
        return len(echoed)


    def observer() -> int:
        return 77


    def main() -> int:
        transport = NativeSocketTransport()
        listener = transport.listen("127.0.0.1", PORT, False, 16)
        if listener < 0:
            return 30
        server_task = vt.spawn(echo_server, transport, listener)
        client_task = vt.spawn(echo_client, transport)
        observer_task = vt.spawn(observer)
        attempts = 0
        while (
            vt.outcome(server_task) == 0
            or vt.outcome(client_task) == 0
            or vt.outcome(observer_task) == 0
        ) and attempts < 200:
            vt.run(1, 256)
            if vt.outcome(server_task) == 0 or vt.outcome(client_task) == 0:
                transport.idle_wait(1)
            attempts = attempts + 1
        transport.close(listener)
        if (
            vt.outcome(server_task) != 1
            or vt.outcome(client_task) != 1
            or vt.outcome(observer_task) != 1
        ):
            return 31
        server_result = vt.result(server_task)
        client_result = vt.result(client_task)
        observer_result = vt.result(observer_task)
        if server_result != len(MESSAGE) or client_result != len(MESSAGE):
            return 32
        if observer_result != 77:
            return 33
        print("PCC1_VTHREAD_TCP_ECHO_OK", vt.io_backend())
        return 0


    main()
    '''
).lstrip()


def _platform_object(tmp_path: Path) -> Path:
    llvm_ir = tmp_path / "gateway_socket.ll"
    pipeline.compile_python(
        str(SOURCE),
        str(llvm_ir),
        emit_llvm_only=True,
        libpython_mode="off",
        python_library=True,
    )
    output = tmp_path / "gateway_socket.o"
    built = subprocess.run(
        ["clang", "-c", str(llvm_ir), "-o", str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    return output


def test_gateway_socket_source_has_no_shared_receive_buffer() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "pcc_socket_recv_static_buf" not in source
    assert "define_global_null_ptr_array" not in source
    assert "pcc_platform_socket_read_observe" in source
    assert "pcc_platform_socket_write_observe" in source
    assert "pcc_platform_tcp_accept_observe" in source
    assert "pcc_platform_socket_peer_text" in source
    assert "pcc_platform_socket_format_numeric_address" in source
    assert "pcc_platform_tcp_connect_start" in source
    assert "socket_getsockopt" in source
    assert "pending_error" in source
    assert "socket_send(fd, scratch, 0, 0)" not in source
    assert "flags & ~nonblocking" not in source


def test_gateway_nonblocking_accept_read_write_and_eof(tmp_path: Path) -> None:
    obj = _platform_object(tmp_path)
    source = tmp_path / "gateway_socket_probe.c"
    executable = tmp_path / "gateway_socket_probe"
    source.write_text(
        r'''
#include <fcntl.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

enum {
    PCC_SOCKET_PROGRESS = 0,
    PCC_SOCKET_WOULD_BLOCK = 1,
    PCC_SOCKET_EOF = 2,
    PCC_SOCKET_CONNECTED = 3
};

int64_t pcc_platform_tcp_listen(const char *, const char *, int64_t);
int64_t pcc_platform_tcp_accept_observe(int64_t, int64_t *);
int64_t pcc_platform_socket_read_observe(
    int64_t, void *, int64_t, int64_t, int64_t *
);
int64_t pcc_platform_socket_write_observe(
    int64_t, const void *, int64_t, int64_t, int64_t *
);
int64_t pcc_platform_socket_ready_observe(int64_t, int64_t, int64_t, int64_t *);
int64_t pcc_platform_socket_sockname(int64_t, void *, int64_t);
int64_t pcc_platform_socket_peer_text(int64_t, char *, int64_t);

int main(void) {
    int64_t listener = pcc_platform_tcp_listen("127.0.0.1", "0", 0);
    if (listener < 0) return 1;
    int flags = fcntl((int)listener, F_GETFL, 0);
    if (flags < 0 || (flags & O_NONBLOCK) == 0) return 2;

    int64_t peer = -1;
    if (pcc_platform_tcp_accept_observe(listener, &peer) != PCC_SOCKET_WOULD_BLOCK)
        return 3;
    if (peer != -1) return 4;

    unsigned char address[128] = {0};
    int64_t address_len = pcc_platform_socket_sockname(
        listener, address, sizeof(address)
    );
    if (address_len <= 0) return 5;

    int control[2];
    if (pipe(control) != 0) return 6;
    pid_t child = fork();
    if (child < 0) return 7;
    if (child == 0) {
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) _exit(20);
        if (connect(fd, (struct sockaddr *)address, (socklen_t)address_len) != 0)
            _exit(21);
        char signal = 0;
        if (read(control[0], &signal, 1) != 1) _exit(22);
        if (send(fd, "hello", 5, 0) != 5) _exit(23);
        char reply[5] = {0};
        if (recv(fd, reply, sizeof(reply), 0) != 5) _exit(24);
        if (memcmp(reply, "world", 5) != 0) _exit(25);
        close(fd);
        _exit(0);
    }

    int64_t ready = 0;
    if (pcc_platform_socket_ready_observe(listener, 1, 2000, &ready)
        != PCC_SOCKET_PROGRESS) return 8;
    if ((ready & 1) == 0) return 9;
    if (pcc_platform_tcp_accept_observe(listener, &peer) != PCC_SOCKET_PROGRESS)
        return 10;
    flags = fcntl((int)peer, F_GETFL, 0);
    if (flags < 0 || (flags & O_NONBLOCK) == 0) return 11;
    char peer_text[64] = {0};
    int64_t peer_text_len = pcc_platform_socket_peer_text(
        peer, peer_text, sizeof(peer_text)
    );
    if (peer_text_len != 9) return 30;
    if (memcmp(peer_text, "127.0.0.1", 9) != 0) return 31;

    char buffer[16] = {0};
    int64_t count = -1;
    if (pcc_platform_socket_read_observe(peer, buffer, sizeof(buffer), 0, &count)
        != PCC_SOCKET_WOULD_BLOCK) return 12;
    if (count != 0) return 13;
    if (write(control[1], "x", 1) != 1) return 14;
    if (pcc_platform_socket_ready_observe(peer, 1, 2000, &ready)
        != PCC_SOCKET_PROGRESS) return 15;
    if (pcc_platform_socket_read_observe(peer, buffer, sizeof(buffer), 0, &count)
        != PCC_SOCKET_PROGRESS) return 16;
    if (count != 5 || memcmp(buffer, "hello", 5) != 0) return 17;
    int64_t sent = 0;
    while (sent < 5) {
        int64_t outcome = pcc_platform_socket_write_observe(
            peer, "world" + sent, 5 - sent, 0, &count
        );
        if (outcome == PCC_SOCKET_WOULD_BLOCK) {
            if (pcc_platform_socket_ready_observe(peer, 4, 2000, &ready)
                != PCC_SOCKET_PROGRESS) return 18;
            continue;
        }
        if (outcome != PCC_SOCKET_PROGRESS || count <= 0) return 19;
        sent += count;
    }

    int status = 0;
    if (waitpid(child, &status, 0) != child) return 26;
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) return 27;
    if (pcc_platform_socket_ready_observe(peer, 1, 2000, &ready)
        != PCC_SOCKET_PROGRESS) return 28;
    if (pcc_platform_socket_read_observe(peer, buffer, sizeof(buffer), 0, &count)
        != PCC_SOCKET_EOF) return 29;

    close(control[0]);
    close(control[1]);
    close((int)peer);
    close((int)listener);
    return 0;
}
''',
        encoding="utf-8",
    )
    built = subprocess.run(
        ["clang", str(source), str(obj), "-o", str(executable)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=30
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_gateway_nonblocking_connect_start_preserves_fd_mode(tmp_path: Path) -> None:
    obj = _platform_object(tmp_path)
    source = tmp_path / "gateway_connect_probe.c"
    executable = tmp_path / "gateway_connect_probe"
    source.write_text(
        r'''
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

enum {
    PCC_SOCKET_PROGRESS = 0,
    PCC_SOCKET_WOULD_BLOCK = 1,
    PCC_SOCKET_CONNECTED = 3
};

int64_t pcc_platform_tcp_listen(const char *, const char *, int64_t);
int64_t pcc_platform_tcp_accept_observe(int64_t, int64_t *);
int64_t pcc_platform_socket_ready_observe(int64_t, int64_t, int64_t, int64_t *);
int64_t pcc_platform_socket_sockname(int64_t, void *, int64_t);
int64_t pcc_platform_tcp_connect_start(const char *, const char *, int64_t *);
int64_t pcc_platform_socket_connect_observe(int64_t, int64_t);

static int64_t accept_ready_peer(int64_t listener) {
    int64_t ready = 0;
    int64_t peer = -1;
    if (
        pcc_platform_socket_ready_observe(listener, 1, 2000, &ready)
        != PCC_SOCKET_PROGRESS
    ) return -1;
    if ((ready & 1) == 0) return -1;
    if (
        pcc_platform_tcp_accept_observe(listener, &peer)
        != PCC_SOCKET_PROGRESS
    ) return -1;
    return peer;
}

int main(void) {
    int64_t listener = pcc_platform_tcp_listen("127.0.0.1", "0", 0);
    if (listener < 0) return 1;
    unsigned char address[128] = {0};
    int64_t length = pcc_platform_socket_sockname(listener, address, sizeof(address));
    if (length < 4) return 2;
    unsigned port = ((unsigned)address[2] << 8) | address[3];
    char port_text[16];
    snprintf(port_text, sizeof(port_text), "%u", port);

    int64_t fd = -1;
    int64_t result = pcc_platform_tcp_connect_start("127.0.0.1", port_text, &fd);
    if (result != 1 && result != 3) return 3;
    if (fd < 0) return 4;
    int flags = fcntl((int)fd, F_GETFL, 0);
    if (flags < 0 || (flags & O_NONBLOCK) == 0) return 5;
    while (result == 1) {
        result = pcc_platform_socket_connect_observe(fd, 2000);
        if (result == -4) result = 1;
    }
    if (result != 3) return 6;
    int peer = (int)accept_ready_peer(listener);
    if (peer < 0) return 7;
    close(peer);
    close((int)fd);

    /* The freestanding resolver may read /etc/hosts, but must never call a
       libc/host DNS resolver.  localhost is the portable hosts-file case. */
    fd = -1;
    result = pcc_platform_tcp_connect_start("localhost", port_text, &fd);
    if (result != 1 && result != 3) return 8;
    if (fd < 0) return 9;
    while (result == 1) {
        result = pcc_platform_socket_connect_observe(fd, 2000);
        if (result == -4) result = 1;
    }
    if (result != 3) return 10;
    peer = (int)accept_ready_peer(listener);
    if (peer < 0) return 11;
    close(peer);
    close((int)fd);
    close((int)listener);
    return 0;
}
''',
        encoding="utf-8",
    )
    built = subprocess.run(
        ["clang", str(source), str(obj), "-o", str(executable)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=30
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_gateway_nonblocking_connect_reports_exact_so_error(tmp_path: Path) -> None:
    obj = _platform_object(tmp_path)
    source = tmp_path / "gateway_connect_so_error_probe.c"
    executable = tmp_path / "gateway_connect_so_error_probe"
    source.write_text(
        r'''
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>

int64_t pcc_platform_tcp_listen(const char *, const char *, int64_t);
int64_t pcc_platform_socket_sockname(int64_t, void *, int64_t);
int64_t pcc_platform_tcp_connect_start(const char *, const char *, int64_t *);
int64_t pcc_platform_socket_connect_observe(int64_t, int64_t);

int main(void) {
    int64_t listener = pcc_platform_tcp_listen("127.0.0.1", "0", 0);
    if (listener < 0) return 1;
    unsigned char address[128] = {0};
    int64_t length = pcc_platform_socket_sockname(listener, address, sizeof(address));
    if (length < 4) return 2;
    unsigned port = ((unsigned)address[2] << 8) | address[3];
    char port_text[16];
    snprintf(port_text, sizeof(port_text), "%u", port);
    close((int)listener);

    int64_t fd = -1;
    int64_t result = pcc_platform_tcp_connect_start(
        "127.0.0.1", port_text, &fd
    );
    int attempts = 0;
    while (result == 1 || result == -EINTR) {
        if (fd < 0 || attempts++ > 8) return 3;
        result = pcc_platform_socket_connect_observe(fd, 2000);
    }
    if (result != -ECONNREFUSED) return 4;
    if (fd >= 0) close((int)fd);
    return 0;
}
''',
        encoding="utf-8",
    )
    built = subprocess.run(
        ["clang", str(source), str(obj), "-o", str(executable)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=30
    )
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.integration
@pytest.mark.pcc_gate(probe="pcc1")
@pytest.mark.xdist_group(name="pcc1_gateway_vthread_tcp_echo")
def test_current_pcc1_self_no_libpython_virtual_thread_tcp_echo(
    tmp_path: Path,
    pcc_py_runtime_archive: Path,
) -> None:
    """Compile a real loopback echo with current pcc1/self and run GC0..4."""
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("a source-current pcc1 is required for the TCP echo gate")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    source = tmp_path / "current_pcc1_vthread_tcp_echo.py"
    executable = tmp_path / "current_pcc1_vthread_tcp_echo"
    source.write_text(
        VIRTUAL_THREAD_TCP_ECHO_SOURCE.replace("__PORT__", str(port)),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    environment["PCC_RUNTIME_ARCHIVE"] = str(pcc_py_runtime_archive)
    command = [
        str(pcc1),
        "--backend",
        "self",
        "--python-libpython=off",
        "--ir-scaffold=on",
        str(source),
        "-o",
        str(executable),
    ]
    built = subprocess.run(
        command,
        cwd=REPO,
        env=environment,
        text=True,
        capture_output=True,
        timeout=600,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    assert executable.is_file()

    for gc_backend in range(5):
        run_environment = dict(environment)
        run_environment["PCC_GC_BACKEND"] = str(gc_backend)
        ran = subprocess.run(
            [str(executable)],
            cwd=REPO,
            env=run_environment,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert ran.returncode == 0, (
            "GC" + str(gc_backend) + ": " + ran.stdout + ran.stderr
        )
        assert ran.stdout.strip() in (
            "PCC1_VTHREAD_TCP_ECHO_OK 1",
            "PCC1_VTHREAD_TCP_ECHO_OK 2",
        )
