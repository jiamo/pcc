from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path


def _build_and_run(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "prog"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "4"
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    return subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )


def test_asyncio_stdlib_objects_compile_without_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "import asyncio\n"
        "\n"
        "async def compute():\n"
        "    await asyncio.sleep(0)\n"
        "    return 41\n"
        "\n"
        "async def plus_one():\n"
        "    return await asyncio.wait_for(compute(), timeout=1) + 1\n"
        "\n"
        "def main():\n"
        "    loop = asyncio.get_event_loop()\n"
        "    fut = loop.create_future()\n"
        "    print(fut.done())\n"
        "    fut.set_result('ready')\n"
        "    print(fut.done(), fut.result())\n"
        "    event = asyncio.Event()\n"
        "    print(event.is_set())\n"
        "    event.set()\n"
        "    print(loop.run_until_complete(event.wait()))\n"
        "    task = asyncio.ensure_future(plus_one())\n"
        "    print(task.done())\n"
        "    print(loop.run_until_complete(task))\n"
        "    print(task.done(), task.result())\n"
        "    q = asyncio.Queue()\n"
        "    q.put_nowait('item')\n"
        "    print(loop.run_until_complete(q.get()))\n"
        "    reader = asyncio.StreamReader()\n"
        "    reader.feed_eof()\n"
        "    print(reader.at_eof())\n"
        "    print(type(asyncio.StreamWriter()).__name__)\n"
        "    print(asyncio.sslproto.SSLProtocol is not None)\n"
        "\n"
        "main()\n",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "False",
        "True ready",
        "False",
        "True",
        "False",
        "42",
        "True 42",
        "item",
        "True",
        "StreamWriter",
        "True",
    ]


def test_asyncio_start_server_binds_tcp_without_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "import asyncio\n"
        "\n"
        "async def main_async():\n"
        "    server = await asyncio.start_server(lambda r, w: None, host='127.0.0.1', port=0)\n"
        "    print(len(server.sockets))\n"
        "    addr = server.sockets[0].getsockname()\n"
        "    print(addr[0], addr[1] > 0)\n"
        "    server.close()\n"
        "    await server.wait_closed()\n"
        "\n"
        "asyncio.run(main_async())\n",
    )
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    assert lines[0] == "1"
    assert lines[1] == "127.0.0.1 True"


def test_asyncio_server_accept_once_is_nonblocking_without_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "import asyncio\n"
        "\n"
        "async def main_async():\n"
        "    server = await asyncio.start_server(lambda r, w: None, host='127.0.0.1', port=0)\n"
        "    print('before')\n"
        "    print(server._accept_once())\n"
        "    print('after')\n"
        "    server.close()\n"
        "    await server.wait_closed()\n"
        "\n"
        "asyncio.run(main_async())\n",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["before", "False", "after"]


def test_asyncio_accepted_socket_waits_for_second_client_write_no_libpython(tmp_path: Path):
    src = tmp_path / "prog.py"
    exe = tmp_path / "prog"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    src.write_text(
        "import asyncio\n"
        "\n"
        "async def handle(reader, writer):\n"
        "    first = await reader.readexactly(4)\n"
        "    writer.write(b'OK')\n"
        "    await writer.drain()\n"
        "    second = await reader.readexactly(3)\n"
        "    writer.write(b'DONE')\n"
        "    await writer.drain()\n"
        "    writer.close()\n"
        "\n"
        "async def main():\n"
        "    await asyncio.start_server(\n"
        "        lambda reader, writer: handle(reader, writer),\n"
        "        host='127.0.0.1',\n"
        f"        port={port},\n"
        "    )\n"
        "\n"
        "loop = asyncio.get_event_loop()\n"
        "loop.run_until_complete(main())\n"
        "loop.run_forever()\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "4"
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    proc = subprocess.Popen(
        [str(exe)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                if proc.poll() is not None:
                    stdout, stderr = proc.communicate(timeout=10)
                    raise AssertionError(f"server exited early\nstdout={stdout}\nstderr={stderr}")
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        with s:
            s.sendall(b"PING")
            assert s.recv(2) == b"OK"
            s.sendall(b"END")
            assert s.recv(4) == b"DONE"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)


def test_asyncio_two_stream_channels_relay_full_duplex_no_libpython(tmp_path: Path):
    upstream_sock = socket.socket()
    upstream_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    upstream_sock.bind(("127.0.0.1", 0))
    upstream_sock.listen(1)
    upstream_port = upstream_sock.getsockname()[1]
    upstream_received: list[bytes] = []

    def serve_upstream() -> None:
        try:
            conn, _addr = upstream_sock.accept()
            with conn:
                data = conn.recv(4)
                upstream_received.append(data)
                conn.sendall(b"PONG")
        finally:
            upstream_sock.close()

    upstream_thread = threading.Thread(target=serve_upstream, daemon=True)
    upstream_thread.start()

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        listen_port = probe.getsockname()[1]

    src = tmp_path / "relay_prog.py"
    exe = tmp_path / "relay_prog"
    src.write_text(
        "import asyncio\n"
        "\n"
        "async def pipe(reader, writer):\n"
        "    while True:\n"
        "        data = await reader.read(65536)\n"
        "        if not data:\n"
        "            break\n"
        "        writer.write(data)\n"
        "        await writer.drain()\n"
        "    writer.close()\n"
        "\n"
        "async def handle(reader, writer):\n"
        "    remote_reader, remote_writer = await asyncio.open_connection(\n"
        "        host='127.0.0.1',\n"
        f"        port={upstream_port},\n"
        "    )\n"
        "    asyncio.ensure_future(pipe(remote_reader, writer))\n"
        "    asyncio.ensure_future(pipe(reader, remote_writer))\n"
        "\n"
        "async def main():\n"
        "    await asyncio.start_server(\n"
        "        lambda reader, writer: handle(reader, writer),\n"
        "        host='127.0.0.1',\n"
        f"        port={listen_port},\n"
        "    )\n"
        "\n"
        "loop = asyncio.get_event_loop()\n"
        "loop.run_until_complete(main())\n"
        "loop.run_forever()\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "4"
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    proc = subprocess.Popen(
        [str(exe)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                client = socket.create_connection(("127.0.0.1", listen_port), timeout=1)
                break
            except OSError:
                if proc.poll() is not None:
                    stdout, stderr = proc.communicate(timeout=10)
                    raise AssertionError(f"server exited early\nstdout={stdout}\nstderr={stderr}")
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        with client:
            client.settimeout(10)
            client.sendall(b"PING")
            assert client.recv(4) == b"PONG"
        upstream_thread.join(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)
        upstream_sock.close()
    assert upstream_received == [b"PING"]


def test_asyncio_idle_relay_does_not_block_concurrent_connection_no_libpython(
    tmp_path: Path,
):
    # Regression for the cooperative fd-relay fix. The native fd relay used to
    # take over the single-threaded event loop with a blocking select() for the
    # whole lifetime of a relayed connection, so once one connection was being
    # relayed (even while fully idle) NO other connection could be served. A
    # browser keeps persistent/keep-alive connections open, so it always tripped
    # this; a single curl that closed immediately did not. The relay is now
    # driven cooperatively, so an open idle connection must not stall a second
    # concurrent connection.
    upstream_sock = socket.socket()
    upstream_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    upstream_sock.bind(("127.0.0.1", 0))
    upstream_sock.listen(8)
    upstream_port = upstream_sock.getsockname()[1]
    stop = threading.Event()

    def echo_conn(conn: socket.socket) -> None:
        try:
            while not stop.is_set():
                data = conn.recv(65536)
                if not data:
                    break
                conn.sendall(data)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def serve_upstream() -> None:
        upstream_sock.settimeout(0.5)
        try:
            while not stop.is_set():
                try:
                    conn, _addr = upstream_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=echo_conn, args=(conn,), daemon=True).start()
        finally:
            try:
                upstream_sock.close()
            except OSError:
                pass

    upstream_thread = threading.Thread(target=serve_upstream, daemon=True)
    upstream_thread.start()

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        listen_port = probe.getsockname()[1]

    src = tmp_path / "idle_relay_prog.py"
    exe = tmp_path / "idle_relay_prog"
    src.write_text(
        "import asyncio\n"
        "\n"
        "async def pipe(reader, writer):\n"
        "    while True:\n"
        "        data = await reader.read(65536)\n"
        "        if not data:\n"
        "            break\n"
        "        writer.write(data)\n"
        "        await writer.drain()\n"
        "    writer.close()\n"
        "\n"
        "async def handle(reader, writer):\n"
        "    remote_reader, remote_writer = await asyncio.open_connection(\n"
        "        host='127.0.0.1',\n"
        f"        port={upstream_port},\n"
        "    )\n"
        "    asyncio.ensure_future(pipe(remote_reader, writer))\n"
        "    asyncio.ensure_future(pipe(reader, remote_writer))\n"
        "\n"
        "async def main():\n"
        "    await asyncio.start_server(\n"
        "        lambda reader, writer: handle(reader, writer),\n"
        "        host='127.0.0.1',\n"
        f"        port={listen_port},\n"
        "    )\n"
        "\n"
        "loop = asyncio.get_event_loop()\n"
        "loop.run_until_complete(main())\n"
        "loop.run_forever()\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "4"
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    proc = subprocess.Popen(
        [str(exe)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    idle = None
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                idle = socket.create_connection(("127.0.0.1", listen_port), timeout=1)
                break
            except OSError:
                if proc.poll() is not None:
                    stdout, stderr = proc.communicate(timeout=10)
                    raise AssertionError(
                        f"server exited early\nstdout={stdout}\nstderr={stderr}"
                    )
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        # `idle` is now an open, fully-idle relayed connection (it sends nothing,
        # so its relay sits waiting for data). Give the loop time to establish
        # the relay, then prove a second connection is still served.
        time.sleep(1.0)
        with socket.create_connection(("127.0.0.1", listen_port), timeout=5) as active:
            active.settimeout(5)
            active.sendall(b"PING")
            assert active.recv(4) == b"PING"
    finally:
        stop.set()
        if idle is not None:
            try:
                idle.close()
            except OSError:
                pass
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)
        try:
            upstream_sock.close()
        except OSError:
            pass


def test_class_object_default_parameter_attr_patch_no_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "class C:\n"
        "    pass\n"
        "def patch(c=C):\n"
        "    c.x = lambda self: 7\n"
        "patch()\n"
        "print(C().x())\n",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "7"


def test_asyncio_stream_reader_default_parameter_patch_no_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "import asyncio\n"
        "def patch(c=asyncio.StreamReader):\n"
        "    c.pcc_patch_probe = lambda self: 'ok'\n"
        "patch()\n"
        "print(asyncio.StreamReader().pcc_patch_probe())\n",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "ok"


def test_asyncio_stream_reader_buffer_extend_persists_no_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "import asyncio\n"
        "reader = asyncio.StreamReader()\n"
        "print(len(reader._buffer))\n"
        "reader._buffer.extend(b'ab')\n"
        "print(len(reader._buffer))\n"
        "print(reader._buffer.take(2))\n",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["0", "2", "b'ab'"]


def test_asyncio_stream_reader_pproxy_probe_helpers_no_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "import asyncio\n"
        "\n"
        "async def main():\n"
        "    reader = asyncio.StreamReader()\n"
        "    reader.feed_data(b'GET /abc\\r\\n\\r\\n')\n"
        "    header = await reader.read_w(4)\n"
        "    print(header)\n"
        "    reader.rollback(header)\n"
        "    print(await reader.read_n(3))\n"
        "    chunk = await reader.readuntil(b'\\r\\n\\r\\n')\n"
        "    print(len(chunk))\n"
        "    print(chunk[:5])\n"
        "    print(chunk == b' /abc\\r\\n\\r\\n')\n"
        "    reader.feed_data(b'HEAD /\\r\\n\\r\\n')\n"
        "    print(await reader.read_until(b'\\r\\n\\r\\n'))\n"
        "    reader.feed_data(b'xy')\n"
        "    reader.feed_eof()\n"
        "    print(await reader.read_w(4))\n"
        "\n"
        "asyncio.get_event_loop().run_until_complete(main())\n",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "b'GET '",
        "b'GET'",
        "9",
        "b' /abc'",
        "True",
        "b'HEAD /\\r\\n\\r\\n'",
        "b'xy'",
    ]


def test_slice_builtin_and_stream_reader_slice_rollback_no_libpython(tmp_path: Path):
    run = _build_and_run(
        tmp_path,
        "import asyncio\n"
        "one = slice(3)\n"
        "print(one.start is None, one.stop, one.step is None)\n"
        "three = slice(1, 5, 2)\n"
        "print(three.start, three.stop, three.step)\n"
        "reader = asyncio.StreamReader()\n"
        "reader.feed_data(b'cd')\n"
        "reader._buffer.__setitem__(slice(0, 0), b'ab')\n"
        "print(reader._buffer.take(4))\n",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "True 3 True",
        "1 5 2",
        "b'abcd'",
    ]


def test_asyncio_open_connection_reads_peer_bytes_no_libpython(tmp_path: Path):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    seen: list[bytes] = []

    def serve_once() -> None:
        try:
            conn, _addr = server.accept()
            with conn:
                seen.append(conn.recv(16))
                conn.sendall(b"ab")
        finally:
            server.close()

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    run = _build_and_run(
        tmp_path,
        "import asyncio\n"
        "\n"
        "async def main():\n"
        f"    reader, writer = await asyncio.open_connection(host='127.0.0.1', port={port})\n"
        "    writer.write(b'hi')\n"
        "    await writer.drain()\n"
        "    print(await reader.read(2))\n"
        "\n"
        "asyncio.get_event_loop().run_until_complete(main())\n",
    )
    thread.join(timeout=5)
    assert seen == [b"hi"]
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "b'ab'"


def test_package_import_initializes_native_stdlib_before_importer(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text("from . import server\n", encoding="utf-8")
    (pkg / "server.py").write_text(
        "import asyncio\n"
        "def patch(c=asyncio.StreamReader):\n"
        "    c.pcc_patch_probe = lambda self: 'ok'\n"
        "patch()\n"
        "print(asyncio.StreamReader().pcc_patch_probe())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "pkg_main"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_GC_BACKEND"] = "4"
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(pkg / "__main__.py"),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "ok"
