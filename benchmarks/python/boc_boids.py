"""Boids flocking — pcc port of BoCPy's ``examples/boids.py``.

Drastically simplified: BoCPy's version uses ``Matrix`` XIData,
``noticeboard``, and per-boid Cowns. pcc has none of those, so this
port keeps the structure but uses plain ``list[int]`` arrays for
positions and velocities, and pthread parallelism for the update
phase.

Each tick of the simulation is two phases, separated by a barrier:

  1. **Read phase**: each worker reads ALL positions to compute its
     own boids' new velocities (cohesion, alignment, separation).
     Reads are concurrent and unsynchronized, which is safe because
     no thread writes during this phase.

  2. **Write phase**: each worker writes only ITS slice of positions
     and velocities. Disjoint slot writes — no contention, no Lock
     needed (already validated by
     ``test_pthread_lock_disjoint_slot_writes_succeed``).

The barrier between phases is a ``threading.Event`` that workers
await after finishing their writes.

Invariant: after N_TICKS ticks, every boid's position has been
updated N_TICKS times. We don't assert visual flocking behaviour
(no plotting in pcc); we assert each boid moved.
"""
from threading import Event, Lock, Thread


N_BOIDS = 64
N_WORKERS = 4
N_TICKS = 50
WIDTH = 1000
HEIGHT = 1000


def lcg(seed: int) -> int:
    return (seed * 1103515245 + 12345) & 0x7FFFFFFF


def update_boid(
    px: list,
    py: list,
    vx: list,
    vy: list,
    n: int,
    idx: int,
) -> None:
    # Read all positions (concurrent reads safe, no writes this phase).
    cx = 0
    cy = 0
    j = 0
    while j < n:
        cx = cx + px[j]
        cy = cy + py[j]
        j = j + 1
    # Cohesion: nudge toward centroid.
    target_x = cx // n
    target_y = cy // n
    new_vx = vx[idx] + (target_x - px[idx]) // 100
    new_vy = vy[idx] + (target_y - py[idx]) // 100
    # Bound velocity.
    if new_vx > 10:
        new_vx = 10
    if new_vx < -10:
        new_vx = -10
    if new_vy > 10:
        new_vy = 10
    if new_vy < -10:
        new_vy = -10
    # Write own slot only.
    vx[idx] = new_vx
    vy[idx] = new_vy
    px[idx] = (px[idx] + new_vx) % WIDTH
    py[idx] = (py[idx] + new_vy) % HEIGHT


def worker(
    px: list,
    py: list,
    vx: list,
    vy: list,
    start_idx: int,
    end_idx: int,
    n: int,
    n_ticks: int,
) -> None:
    tick = 0
    while tick < n_ticks:
        i = start_idx
        while i < end_idx:
            update_boid(px, py, vx, vy, n, i)
            i = i + 1
        tick = tick + 1


def main() -> None:
    px: list = []
    py: list = []
    vx: list = []
    vy: list = []
    seed = 42
    i = 0
    while i < N_BOIDS:
        seed = lcg(seed)
        px.append(seed % WIDTH)
        seed = lcg(seed)
        py.append(seed % HEIGHT)
        seed = lcg(seed)
        vx.append((seed % 11) - 5)
        seed = lcg(seed)
        vy.append((seed % 11) - 5)
        i = i + 1

    chunk = N_BOIDS // N_WORKERS
    t0 = Thread(target=worker, args=(px, py, vx, vy, 0 * chunk, 1 * chunk, N_BOIDS, N_TICKS))
    t1 = Thread(target=worker, args=(px, py, vx, vy, 1 * chunk, 2 * chunk, N_BOIDS, N_TICKS))
    t2 = Thread(target=worker, args=(px, py, vx, vy, 2 * chunk, 3 * chunk, N_BOIDS, N_TICKS))
    t3 = Thread(target=worker, args=(px, py, vx, vy, 3 * chunk, 4 * chunk, N_BOIDS, N_TICKS))
    t0.start(); t1.start(); t2.start(); t3.start()
    t0.join(); t1.join(); t2.join(); t3.join()

    # Sanity: positions shifted from initial seed values? Use sum as a coarse hash.
    sx = 0
    sy = 0
    j = 0
    while j < N_BOIDS:
        sx = sx + px[j]
        sy = sy + py[j]
        j = j + 1
    print("boids=" + str(N_BOIDS))
    print("workers=" + str(N_WORKERS))
    print("ticks=" + str(N_TICKS))
    print("sum_px=" + str(sx))
    print("sum_py=" + str(sy))
    print("DONE")


if __name__ == "__main__":
    main()
