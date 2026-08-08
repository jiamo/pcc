"""Oracle tests for pcc.dist.session (D-P0-DIST-SESSION).

Covers rank identity/equality, mesh<->coord bijection, DRef ownership +
serialization round-trip, and EXPLICIT rejection of every networking mode.
"""
import pytest

from pcc.dist import session
from pcc.dist.results import DistUnavailableError


# --- Rank identity ---------------------------------------------------------
def test_rank_identity_is_index_only():
    assert session.Rank(1, 4) == session.Rank(1, 4)
    assert session.Rank(1, 4) != session.Rank(2, 4)
    # Same index in different-size worlds keeps the same identity/hash.
    assert session.Rank(1, 4) == session.Rank(1, 8)
    assert hash(session.Rank(1, 4)) == hash(session.Rank(1, 8))


def test_rank_sorts_by_index():
    w = session.World(4)
    assert sorted([w.rank(3), w.rank(0), w.rank(2), w.rank(1)]) == list(w.ranks())


def test_rank_range_validation():
    with pytest.raises(session.SessionError):
        session.Rank(4, 4)
    with pytest.raises(session.SessionError):
        session.Rank(-1, 4)
    with pytest.raises(session.SessionError):
        session.Rank(0, 0)


def test_rank_leader_and_label():
    w = session.World(3)
    assert w.leader.is_leader
    assert not w.rank(1).is_leader
    assert w.rank(1).label() == "rank1/3"


def test_world_membership_and_iter():
    w = session.World(3)
    assert len(w) == 3
    assert list(w) == [w.rank(0), w.rank(1), w.rank(2)]
    assert w.rank(2) in w
    assert session.World(3) == session.World(3)
    assert session.World(3) != session.World(4)


# --- DeviceMesh bijection --------------------------------------------------
def test_mesh_rank_coord_bijection_2d():
    w = session.World(6)
    m = session.DeviceMesh((2, 3), world=w)
    seen = set()
    for r in w.ranks():
        coord = m.coord_of(r)
        assert m.rank_of(coord) == r  # exact inverse
        seen.add(coord)
    assert len(seen) == 6  # bijection: every coord distinct


def test_mesh_rowmajor_layout():
    w = session.World(6)
    m = session.DeviceMesh((2, 3), world=w)
    # row-major: rank 4 -> (1, 1)
    assert m.coord_of(w.rank(4)) == (1, 1)
    assert m.rank_of((1, 1)) == w.rank(4)


def test_mesh_shape_must_match_world():
    with pytest.raises(session.SessionError):
        session.DeviceMesh((2, 3), world=session.World(5))


def test_mesh_rejects_bad_extents_and_axes():
    with pytest.raises(session.SessionError):
        session.DeviceMesh(())
    with pytest.raises(session.SessionError):
        session.DeviceMesh((0, 2))
    with pytest.raises(session.SessionError):
        session.DeviceMesh((2, 2), axis_names=("dp",))  # wrong count
    with pytest.raises(session.SessionError):
        session.DeviceMesh((2, 2), axis_names=("dp", "dp"))  # not unique


def test_mesh_ranks_along_axis():
    w = session.World(4)
    m = session.DeviceMesh((2, 2), world=w, axis_names=("dp", "tp"))
    # tp peers of (row=1, *)
    assert m.ranks_along_axis("tp", (1, 0)) == (w.rank(2), w.rank(3))
    assert m.ranks_along_axis("dp", (0, 1)) == (w.rank(1), w.rank(3))
    assert m.axis_index("tp") == 1
    with pytest.raises(session.SessionError):
        m.axis_index("nope")


def test_mesh_coord_out_of_range():
    m = session.DeviceMesh((2, 2))
    with pytest.raises(session.SessionError):
        m.rank_of((2, 0))
    with pytest.raises(session.SessionError):
        m.rank_of((0,))  # wrong ndim


# --- DRef ownership + serialization ---------------------------------------
def test_dref_ownership_and_equality():
    w = session.World(4)
    a = session.DRef(w.rank(2), 5, "grad")
    b = session.DRef(w.rank(2), 5, "different-label")
    c = session.DRef(w.rank(3), 5, "grad")
    assert a == b  # equality is (owner, obj_id); label is descriptive
    assert a != c
    assert a.is_owned_by(2)
    assert not a.is_owned_by(3)
    assert hash(a) == hash(b)


def test_dref_serialize_roundtrip():
    d = session.DRef(session.Rank(3, 8), 42, "kv_block")
    blob = d.serialize()
    back = session.DRef.deserialize(blob)
    assert back == d
    assert back.label == "kv_block"
    assert back.owner.world_size == 8


def test_dref_deserialize_rejects_garbage():
    with pytest.raises(session.SessionError):
        session.DRef.deserialize("not-a-dref")
    with pytest.raises(session.SessionError):
        session.DRef.deserialize("dref:garbage")


def test_dref_validation():
    with pytest.raises(session.SessionError):
        session.DRef(session.Rank(0, 2), -1, "x")
    with pytest.raises(session.SessionError):
        session.DRef(session.Rank(0, 2), 0, "")


def test_session_mints_monotonic_ids_per_rank():
    w = session.World(3)
    s = session.PCCDistSession(w)
    a = s.new_ref(1, "p")
    b = s.new_ref(1, "p")
    c = s.new_ref(2, "p")
    assert a.obj_id == 0 and b.obj_id == 1  # per-owner monotonic
    assert c.obj_id == 0  # independent counter per owner
    assert a != b


def test_session_rejects_unknown_owner():
    s = session.PCCDistSession(session.World(2))
    with pytest.raises(session.SessionError):
        s.new_ref(5, "p")


# --- explicit networking rejection ----------------------------------------
def test_connect_rejects_every_network_mode():
    s = session.PCCDistSession(session.World(2))
    modes = session.network_modes()
    assert set(modes) >= {"bonjour", "tcp-ring", "quic", "jaccl-rdma"}
    for mode in modes:
        result = s.connect(mode)
        assert result.skipped, f"{mode} must be SKIPPED_WITH_REASON"
        assert result.reason.strip(), f"{mode} skip must carry a reason"


def test_connect_unknown_mode_is_loud():
    s = session.PCCDistSession(session.World(2))
    with pytest.raises(session.SessionError):
        s.connect("infiniband")  # typo/unknown must raise, not silently skip


def test_require_connected_always_raises():
    s = session.PCCDistSession(session.World(2))
    with pytest.raises(DistUnavailableError):
        s.require_connected("tcp-ring")


def test_network_capabilities_all_skipped():
    s = session.PCCDistSession(session.World(2))
    caps = s.network_capabilities()
    assert caps and all(c.skipped for c in caps)


def test_session_manifest_roundtrips_shape():
    import json
    w = session.World(4)
    s = session.PCCDistSession(w, session.DeviceMesh((2, 2), world=w, axis_names=("dp", "tp")))
    payload = json.loads(session.session_manifest(s))
    assert payload["world_size"] == 4
    assert payload["mesh_shape"] == [2, 2]
    assert payload["mesh_axes"] == ["dp", "tp"]
