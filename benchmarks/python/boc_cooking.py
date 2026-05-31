"""Cooking pipeline — pcc port of BoCPy's ``examples/cooking_boc.py``.

BoCPy's original models a cook coordinating a knife (chop), bowl
(mix), pan (heat), and plate (serve) via Cowns and ``@when``. This
port keeps the same pipeline shape but with pcc's ``threading.Lock``
on each stage's input queue.

Stages:
    chopper  -> mixer  -> cooker  -> server

Each stage is one worker thread that pulls a token from its input
queue (under Lock), does a small CPU-bound transformation, and
pushes to the next stage's input queue (under Lock). The
``server`` stage drops tokens into a final ``done`` count.

Items pumped through: N_ITEMS. Final ``done`` count must equal
N_ITEMS — proves end-to-end correctness across 4 stages with
contention on each handoff.

This exercises:
- producer/consumer Lock semantics (now correct after the
  2026-05-08 fix to ``layer1.py``)
- ``list.append`` and ``list.pop`` under one shared Lock
- multi-stage parallelism (each stage runs concurrently with
  the others, so total throughput overlaps work across stages)
"""
from threading import Lock, Thread


N_ITEMS = 200
WORK_PER_STAGE = 30


def cpu_work(rounds: int) -> int:
    x = 1
    i = 0
    while i < rounds:
        x = (x * 1664525 + 1013904223) & 0xFFFFFFFF
        i = i + 1
    return x


def chopper(
    in_lock: Lock, in_q: list,
    out_lock: Lock, out_q: list,
    end_flag: list,
) -> None:
    while True:
        in_lock.acquire()
        if len(in_q) > 0:
            item = in_q[0]
            del in_q[0]
            in_lock.release()
            _ = cpu_work(WORK_PER_STAGE)
            out_lock.acquire()
            out_q.append(item)
            out_lock.release()
        else:
            done = end_flag[0]
            in_lock.release()
            if done == 1:
                return


def stage_worker(
    in_lock: Lock, in_q: list,
    out_lock: Lock, out_q: list,
    end_flag: list,
) -> None:
    while True:
        in_lock.acquire()
        if len(in_q) > 0:
            item = in_q[0]
            del in_q[0]
            in_lock.release()
            _ = cpu_work(WORK_PER_STAGE)
            out_lock.acquire()
            out_q.append(item)
            out_lock.release()
        else:
            done = end_flag[0]
            in_lock.release()
            if done == 1:
                return


def server(
    in_lock: Lock, in_q: list,
    served: list,
    end_flag: list,
) -> None:
    while True:
        in_lock.acquire()
        if len(in_q) > 0:
            del in_q[0]
            served[0] = served[0] + 1
            in_lock.release()
        else:
            done = end_flag[0]
            in_lock.release()
            if done == 1:
                return


def main() -> None:
    chop_lock = Lock()
    mix_lock = Lock()
    cook_lock = Lock()
    serve_lock = Lock()

    chop_q: list = []
    mix_q: list = []
    cook_q: list = []
    serve_q: list = []
    served: list = [0]
    end_flag: list = [0]

    # Producer: prime the chop queue with N_ITEMS items.
    i = 0
    while i < N_ITEMS:
        chop_q.append(i)
        i = i + 1

    t_chop = Thread(target=stage_worker, args=(chop_lock, chop_q, mix_lock, mix_q, end_flag))
    t_mix = Thread(target=stage_worker, args=(mix_lock, mix_q, cook_lock, cook_q, end_flag))
    t_cook = Thread(target=stage_worker, args=(cook_lock, cook_q, serve_lock, serve_q, end_flag))
    t_serve = Thread(target=server, args=(serve_lock, serve_q, served, end_flag))

    t_chop.start(); t_mix.start(); t_cook.start(); t_serve.start()

    # Drain: poll the served counter. No spin cap — we want to wait
    # until all items have made it through all 4 stages. The cpu_work
    # in each loop iteration keeps the polling cheap relative to the
    # stage-worker rate (~30 work units per stage per item).
    while True:
        serve_lock.acquire()
        s = served[0]
        serve_lock.release()
        if s >= N_ITEMS:
            end_flag[0] = 1
            break
        _ = cpu_work(200)

    t_chop.join(); t_mix.join(); t_cook.join(); t_serve.join()

    print("items=" + str(N_ITEMS))
    print("served=" + str(served[0]))
    if served[0] == N_ITEMS:
        print("PASS")
    else:
        print("FAIL")


if __name__ == "__main__":
    main()
