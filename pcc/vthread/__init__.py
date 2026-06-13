"""CPU-only oracles for scalable virtual-thread scheduling structures.

These modules validate the *algorithms* that a later runtime C slice will
mirror. They are ORACLES, not the runtime: no threads, no syscalls, no
libpython, no wall-clock. See ``docs/design/pcc-vthread-oracles.md`` for the
C-mirror plan and the claim boundary.

* :mod:`pcc.vthread.timer_oracle` — scalable timer structure (min-heap with
  lazy cancellation) replacing the O(n)-insert sorted linked-list timer queue.
* :mod:`pcc.vthread.io_waitset_oracle` — IO waitset abstraction with a
  poll-style level-triggered fallback and a kqueue-style edge/level readiness
  simulation, replacing the per-poll O(n) fd scan.
"""

from __future__ import annotations

__all__ = ["timer_oracle", "io_waitset_oracle"]
