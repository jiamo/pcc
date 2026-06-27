from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from pcc1_gate import find_current_pcc1, skip_or_fail_no_current_pcc1


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    for parent in path.parents:
        if (parent / "pcc" / "__main__.py").exists():
            return parent
    raise AssertionError(f"cannot find repo root from {path}")


REPO = _repo_root()
PROJECT = REPO / "projects" / "python-proxy"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_listening(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"port {port} did not start listening")


class _OneShotHttpServer:
    def __init__(self, max_connections: int = 1) -> None:
        self.max_connections = max_connections
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self._sock.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        for _ in range(self.max_connections):
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(5)
                data = bytearray()
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data.extend(chunk)
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Length: 2\r\n"
                    b"Connection: close\r\n"
                    b"\r\n"
                    b"ok"
                )
        self._sock.close()


class _OneShotSocks5Server:
    def __init__(self, max_connections: int = 1) -> None:
        self.max_connections = max_connections
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self._sock.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    @staticmethod
    def _read_exact(conn: socket.socket, n: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < n:
            chunk = conn.recv(n - len(chunks))
            if not chunk:
                raise EOFError("short socks read")
            chunks.extend(chunk)
        return bytes(chunks)

    def _serve(self) -> None:
        for _ in range(self.max_connections):
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(10)
                header = self._read_exact(conn, 2)
                nmethods = header[1]
                self._read_exact(conn, nmethods)
                conn.sendall(b"\x05\x00")
                req = self._read_exact(conn, 4)
                atyp = req[3]
                if atyp == 1:
                    host = socket.inet_ntoa(self._read_exact(conn, 4))
                elif atyp == 3:
                    host_len = self._read_exact(conn, 1)[0]
                    host = self._read_exact(conn, host_len).decode()
                else:
                    raise AssertionError(f"unsupported SOCKS atyp {atyp}")
                port = int.from_bytes(self._read_exact(conn, 2), "big")
                with socket.create_connection((host, port), timeout=10) as remote:
                    conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
                    data = bytearray()
                    while b"\r\n\r\n" not in data:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data.extend(chunk)
                    remote.sendall(data)
                    while True:
                        chunk = remote.recv(4096)
                        if not chunk:
                            break
                        conn.sendall(chunk)
        self._sock.close()


class _HoldTcpServer:
    def __init__(self) -> None:
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self._sock.listen(4)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            while not self._stop.wait(0.05):
                pass


def test_python_proxy_shutdown_drains_channel_tasks() -> None:
    assert (PROJECT / "pproxy" / "server.py").exists()
    remote = _HoldTcpServer()
    remote.start()
    listen_port = _free_port()
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PYTHONPATH"] = str(PROJECT)
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "python",
            "-m",
            "pproxy",
            "-l",
            f"http://:{listen_port}",
            "-r",
            "direct://",
        ],
        cwd=REPO,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
    )
    client = None
    try:
        _wait_listening(listen_port)
        client = socket.create_connection(("127.0.0.1", listen_port), timeout=5)
        client.sendall(
            f"CONNECT 127.0.0.1:{remote.port} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{remote.port}\r\n"
            "\r\n"
            .encode()
        )
        assert b"200 Connection established" in client.recv(4096)
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)
    finally:
        if client is not None:
            client.close()
        remote.close()
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=5)
    combined = (stdout or "") + (stderr or "")
    assert "Task was destroyed but it is pending" not in combined
    assert "BaseProtocol.channel" not in combined


# Both moving/forwarding backends run this gate: the pcc1 parallel frontend
# worker crash it guards against (owned-local rebind slot missing its GC
# frame root -> stale release after relocation) reproduced identically on
# backend #3 (generational) and #4 (colored-relocating).
@pytest.mark.parametrize("gc_backend", ["3", "4"])
def test_pcc1_runs_project_python_proxy_test_mode_against_local_http(
    gc_backend: str,
) -> None:
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary for projects/python-proxy gate")
    server = _OneShotHttpServer(max_connections=2)
    server.start()
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = gc_backend
    socks = _OneShotSocks5Server(max_connections=2)
    socks.start()
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pproxy",
            "--test",
            f"http://127.0.0.1:{server.port}/",
            "-r",
            f"socks://127.0.0.1:{socks.port}",
        ],
        cwd=PROJECT,
        env=env,
        text=True,
        capture_output=True,
        timeout=240,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "HTTP/1.1 200 OK" in proc.stdout
    assert "ok" in proc.stdout
    assert "============ success ============" in proc.stdout
