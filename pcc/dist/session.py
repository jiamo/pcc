"""D-P0-DIST-SESSION: world / mesh / rank / DRef identity model (local-only).

TVM Disco maps a distributed program onto a ``Session`` that owns workers,
addresses each remote object with a ``DRef`` (device reference), and describes
placement with a device mesh. This module ports **only the identity and
placement metadata** of that model into a single process:

    World(size)          the set of ranks that exist
    Rank                 a stable identity in [0, size)
    DeviceMesh(shape)    a bijection between ranks and N-D coordinates
    DRef                 (owner rank, object id, serialization label)
    PCCDistSession       binds a world + mesh; mints DRefs; refuses networking

No sockets, no processes, no threads. Every "go distributed" request is
rejected with a :class:`~pcc.dist.results.CapabilityResult` skip (or
:class:`~pcc.dist.results.DistUnavailableError`) until a real transport exists.

Standalone-importable: ``import pcc.dist.session``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from .results import CapabilityResult, DistUnavailableError, skipped

# Networking modes that this metadata slice knows about but cannot provide.
# session.connect(mode) returns a SKIPPED_WITH_REASON result for each.
_NETWORK_MODES: dict[str, str] = {
    "insecure-dev": (
        "insecure-dev is a local single-process alias; it carries no socket "
        "transport in this metadata slice (D-P0-DIST-TRANSPORT owns real modes)"
    ),
    "bonjour": "Bonjour service discovery is unavailable: no Network.framework transport is implemented",
    "tcp-ring": "TCP ring transport is unavailable: no sockets are opened in this local-only slice",
    "quic": "QUIC transport is unavailable: no Network.framework/QUIC backend is implemented",
    "jaccl-rdma": "JACCL/Thunderbolt-RDMA transport is unavailable: no RDMA backend is implemented",
}


class SessionError(Exception):
    """Raised for malformed world / mesh / rank / DRef metadata."""


@dataclass(frozen=True)
class Rank:
    """A stable participant identity in ``[0, world_size)``.

    Equality and ordering are by ``index`` alone, so ranks can key dicts and be
    sorted deterministically. ``world_size`` is retained for range validation
    and for nicer diagnostics, but two ``Rank(index=i, ...)`` compare equal iff
    their indices match. We define comparisons manually (rather than
    ``order=True``) precisely because identity is index-only, not the full
    ``(index, world_size)`` tuple dataclass ordering would use.
    """

    index: int
    world_size: int

    def __post_init__(self) -> None:
        if self.world_size <= 0:
            raise SessionError(f"world_size must be positive, got {self.world_size}")
        if not (0 <= self.index < self.world_size):
            raise SessionError(
                f"rank index {self.index} out of range for world_size {self.world_size}"
            )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Rank):
            return NotImplemented
        return self.index == other.index

    def __lt__(self, other: "Rank") -> bool:  # type: ignore[override]
        if not isinstance(other, Rank):
            return NotImplemented
        return self.index < other.index

    def __hash__(self) -> int:
        return hash(self.index)

    @property
    def is_leader(self) -> bool:
        return self.index == 0

    def label(self) -> str:
        return f"rank{self.index}/{self.world_size}"


class World:
    """The immutable set of ranks in a (local) distributed run."""

    def __init__(self, size: int) -> None:
        if size <= 0:
            raise SessionError(f"world size must be positive, got {size}")
        self._size = size

    @property
    def size(self) -> int:
        return self._size

    def rank(self, index: int) -> Rank:
        return Rank(index, self._size)

    @property
    def leader(self) -> Rank:
        return self.rank(0)

    def ranks(self) -> tuple[Rank, ...]:
        return tuple(Rank(i, self._size) for i in range(self._size))

    def __iter__(self) -> Iterator[Rank]:
        return iter(self.ranks())

    def __len__(self) -> int:
        return self._size

    def __contains__(self, item: object) -> bool:
        return isinstance(item, Rank) and 0 <= item.index < self._size

    def __eq__(self, other: object) -> bool:
        return isinstance(other, World) and other._size == self._size

    def __hash__(self) -> int:
        return hash(("World", self._size))

    def __repr__(self) -> str:
        return f"World(size={self._size})"


class DeviceMesh:
    """A bijection between ranks and N-dimensional coordinates (row-major).

    ``shape`` is a tuple of positive extents whose product equals the world
    size. ``coord_of(rank)`` and ``rank_of(coord)`` are exact inverses, which
    is the invariant the tests pin down. ``axis_names`` is optional metadata
    (e.g. ``("dp", "tp")``) used by the sharding oracle.
    """

    def __init__(
        self,
        shape: Sequence[int],
        *,
        world: World | None = None,
        axis_names: Sequence[str] | None = None,
    ) -> None:
        shape = tuple(int(x) for x in shape)
        if not shape:
            raise SessionError("mesh shape must have at least one axis")
        if any(x <= 0 for x in shape):
            raise SessionError(f"mesh extents must be positive, got {shape}")
        self._shape = shape
        self._size = math.prod(shape)
        if world is not None and world.size != self._size:
            raise SessionError(
                f"mesh shape {shape} has {self._size} cells but world size is {world.size}"
            )
        self._world = world if world is not None else World(self._size)
        if axis_names is not None:
            axis_names = tuple(str(a) for a in axis_names)
            if len(axis_names) != len(shape):
                raise SessionError(
                    f"axis_names {axis_names} does not match shape rank {len(shape)}"
                )
            if len(set(axis_names)) != len(axis_names):
                raise SessionError(f"axis_names must be unique, got {axis_names}")
        self._axis_names = axis_names

    @property
    def shape(self) -> tuple[int, ...]:
        return self._shape

    @property
    def size(self) -> int:
        return self._size

    @property
    def world(self) -> World:
        return self._world

    @property
    def ndim(self) -> int:
        return len(self._shape)

    @property
    def axis_names(self) -> tuple[str, ...] | None:
        return self._axis_names

    def coord_of(self, rank: Rank | int) -> tuple[int, ...]:
        index = rank.index if isinstance(rank, Rank) else int(rank)
        if not (0 <= index < self._size):
            raise SessionError(f"rank index {index} out of range for mesh size {self._size}")
        coord: list[int] = []
        for extent in reversed(self._shape):
            coord.append(index % extent)
            index //= extent
        return tuple(reversed(coord))

    def rank_of(self, coord: Sequence[int]) -> Rank:
        coord = tuple(int(x) for x in coord)
        if len(coord) != self.ndim:
            raise SessionError(
                f"coord {coord} has rank {len(coord)}, expected {self.ndim}"
            )
        index = 0
        for c, extent in zip(coord, self._shape):
            if not (0 <= c < extent):
                raise SessionError(f"coord component {c} out of range for extent {extent}")
            index = index * extent + c
        return self._world.rank(index)

    def axis_index(self, name: str) -> int:
        if self._axis_names is None:
            raise SessionError("mesh has no axis names")
        try:
            return self._axis_names.index(name)
        except ValueError:
            raise SessionError(f"unknown axis {name!r}; have {self._axis_names}") from None

    def ranks_along_axis(self, axis: int | str, fixed: Sequence[int]) -> tuple[Rank, ...]:
        """Ranks that vary only along ``axis`` with the other coords fixed.

        ``fixed`` supplies the full coordinate; its component at ``axis`` is
        ignored and swept over the axis extent. This is what a collective group
        (e.g. tensor-parallel peers) needs.
        """
        axis_i = self.axis_index(axis) if isinstance(axis, str) else int(axis)
        if not (0 <= axis_i < self.ndim):
            raise SessionError(f"axis {axis_i} out of range for ndim {self.ndim}")
        if len(fixed) != self.ndim:
            raise SessionError(f"fixed coord {tuple(fixed)} must have ndim {self.ndim}")
        out: list[Rank] = []
        base = list(int(x) for x in fixed)
        for c in range(self._shape[axis_i]):
            base[axis_i] = c
            out.append(self.rank_of(base))
        return tuple(out)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, DeviceMesh)
            and other._shape == self._shape
            and other._axis_names == self._axis_names
        )

    def __hash__(self) -> int:
        return hash(("DeviceMesh", self._shape, self._axis_names))

    def __repr__(self) -> str:
        names = f", axis_names={self._axis_names}" if self._axis_names else ""
        return f"DeviceMesh(shape={self._shape}{names})"


@dataclass(frozen=True)
class DRef:
    """A distributed reference: owner rank + object id + serialization label.

    A ``DRef`` names an object that *lives on* ``owner`` in the distributed
    world. ``obj_id`` is unique within an owner (the session mints monotonically
    increasing ids per rank). ``label`` is a human/serialization tag describing
    the payload class (e.g. ``"grad_bucket"``). Two DRefs are equal iff owner
    and obj_id match; the label is descriptive metadata carried along.
    """

    owner: Rank
    obj_id: int
    label: str

    def __post_init__(self) -> None:
        if self.obj_id < 0:
            raise SessionError(f"obj_id must be non-negative, got {self.obj_id}")
        if not self.label:
            raise SessionError("DRef label must be a non-empty string")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DRef):
            return NotImplemented
        return self.owner == other.owner and self.obj_id == other.obj_id

    def __hash__(self) -> int:
        return hash((self.owner.index, self.obj_id))

    def is_owned_by(self, rank: Rank | int) -> bool:
        index = rank.index if isinstance(rank, Rank) else int(rank)
        return self.owner.index == index

    def serialize(self) -> str:
        """Round-trip-stable wire label: ``dref:<owner>/<size>:<id>:<label>``."""
        return f"dref:{self.owner.index}/{self.owner.world_size}:{self.obj_id}:{self.label}"

    @classmethod
    def deserialize(cls, blob: str) -> "DRef":
        if not blob.startswith("dref:"):
            raise SessionError(f"not a DRef label: {blob!r}")
        body = blob[len("dref:"):]
        try:
            rank_part, id_part, label = body.split(":", 2)
            owner_index, world_size = (int(x) for x in rank_part.split("/", 1))
            obj_id = int(id_part)
        except (ValueError, TypeError) as exc:
            raise SessionError(f"malformed DRef label: {blob!r}") from exc
        return cls(Rank(owner_index, world_size), obj_id, label)


class PCCDistSession:
    """A local-only session binding a world + optional mesh, minting DRefs.

    This is the metadata analog of TVM Disco's ``Session``. It does NOT own
    workers, sockets, or processes. ``connect()`` always reports the requested
    networking mode as unavailable, which is the explicit rejection the goal
    row requires until a transport exists.
    """

    def __init__(self, world: World, mesh: DeviceMesh | None = None) -> None:
        if mesh is not None and mesh.world.size != world.size:
            raise SessionError(
                f"mesh size {mesh.world.size} does not match world size {world.size}"
            )
        self._world = world
        self._mesh = mesh
        self._next_id: dict[int, int] = {r.index: 0 for r in world.ranks()}

    @property
    def world(self) -> World:
        return self._world

    @property
    def mesh(self) -> DeviceMesh | None:
        return self._mesh

    def with_mesh(self, shape: Sequence[int], **kw: object) -> "PCCDistSession":
        mesh = DeviceMesh(shape, world=self._world, **kw)  # type: ignore[arg-type]
        return PCCDistSession(self._world, mesh)

    def new_ref(self, owner: Rank | int, label: str) -> DRef:
        """Mint a fresh DRef owned by ``owner`` with a monotonically-unique id."""
        index = owner.index if isinstance(owner, Rank) else int(owner)
        if index not in self._next_id:
            raise SessionError(f"rank {index} is not in this session's world")
        obj_id = self._next_id[index]
        self._next_id[index] = obj_id + 1
        return DRef(self._world.rank(index), obj_id, label)

    def connect(self, mode: str = "insecure-dev") -> CapabilityResult:
        """Attempt to enter a networking mode. Always UNAVAILABLE in this slice.

        Returns a ``SKIPPED_WITH_REASON`` :class:`CapabilityResult` for every
        known mode, and raises :class:`~pcc.dist.results.SessionError` for an
        unknown mode name (so a typo is loud, not silently skipped).
        """
        if mode not in _NETWORK_MODES:
            raise SessionError(
                f"unknown networking mode {mode!r}; known modes: {sorted(_NETWORK_MODES)}"
            )
        return skipped(f"session.connect[{mode}]", _NETWORK_MODES[mode], mode=mode)

    def require_connected(self, mode: str = "insecure-dev") -> None:
        """Hard-refuse networking. Always raises :class:`DistUnavailableError`."""
        result = self.connect(mode)
        raise DistUnavailableError(result.capability, result.reason)

    def network_capabilities(self) -> tuple[CapabilityResult, ...]:
        return tuple(self.connect(m) for m in _NETWORK_MODES)


def network_modes() -> tuple[str, ...]:
    """The networking mode names this metadata slice recognizes (all unavailable)."""
    return tuple(_NETWORK_MODES)


def session_manifest(session: PCCDistSession) -> str:
    """Serialize a session's identity metadata to a stable JSON string."""
    payload = {
        "world_size": session.world.size,
        "mesh_shape": list(session.mesh.shape) if session.mesh else None,
        "mesh_axes": list(session.mesh.axis_names) if session.mesh and session.mesh.axis_names else None,
    }
    return json.dumps(payload, sort_keys=True)
