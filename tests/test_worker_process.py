from __future__ import annotations

from tests import worker_process


class _FakeConn:
    def __init__(self, *, payload=None, poll_result=False):
        self.payload = payload
        self.poll_result = poll_result
        self.closed = False

    def poll(self, timeout):
        del timeout
        return self.poll_result

    def recv(self):
        return self.payload

    def close(self):
        self.closed = True


class _FakeProc:
    def __init__(self, *, alive_states, exitcode=0):
        self._alive_states = list(alive_states)
        self.exitcode = exitcode
        self.started = False
        self.terminated = False
        self.killed = False
        self.closed = False
        self.join_calls = []

    def start(self):
        self.started = True

    def join(self, timeout=None):
        self.join_calls.append(timeout)

    def is_alive(self):
        if self._alive_states:
            return self._alive_states.pop(0)
        return False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.exitcode = -9

    def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, parent_conn, child_conn, proc):
        self.parent_conn = parent_conn
        self.child_conn = child_conn
        self.proc = proc
        self.process_target = None
        self.process_args = None

    def Pipe(self, duplex=False):
        assert duplex is False
        return self.parent_conn, self.child_conn

    def Process(self, target, args):
        self.process_target = target
        self.process_args = args
        return self.proc


def test_run_worker_process_returns_payload_and_closes_resources(monkeypatch):
    parent_conn = _FakeConn(payload={"returncode": 0}, poll_result=True)
    child_conn = _FakeConn()
    proc = _FakeProc(alive_states=[False], exitcode=0)
    ctx = _FakeContext(parent_conn, child_conn, proc)

    monkeypatch.setattr(worker_process.multiprocessing, "get_context", lambda method: ctx)
    monkeypatch.setattr(
        worker_process.multiprocessing,
        "get_all_start_methods",
        lambda: ["fork", "spawn"],
    )
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setattr(worker_process.__main__, "__file__", None, raising=False)

    result = worker_process.run_worker_process(lambda conn: None, tuple(), 5)

    assert result.timed_out is False
    assert result.exitcode == 0
    assert result.payload == {"returncode": 0}
    assert proc.started is True
    assert proc.terminated is False
    assert proc.killed is False
    assert proc.closed is True
    assert parent_conn.closed is True
    assert child_conn.closed is True


def test_run_worker_process_timeout_terminates_kills_and_closes_resources(monkeypatch):
    parent_conn = _FakeConn()
    child_conn = _FakeConn()
    proc = _FakeProc(alive_states=[True, True], exitcode=None)
    ctx = _FakeContext(parent_conn, child_conn, proc)

    monkeypatch.setattr(worker_process.multiprocessing, "get_context", lambda method: ctx)
    monkeypatch.setattr(
        worker_process.multiprocessing,
        "get_all_start_methods",
        lambda: ["fork", "spawn"],
    )
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setattr(worker_process.__main__, "__file__", None, raising=False)

    result = worker_process.run_worker_process(lambda conn: None, tuple(), 1)

    assert result.timed_out is True
    assert result.exitcode == -9
    assert proc.started is True
    assert proc.terminated is True
    assert proc.killed is True
    assert proc.closed is True
    assert proc.join_calls == [1, 1, 1]
    assert parent_conn.closed is True
    assert child_conn.closed is True
