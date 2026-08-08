"""Oracle tests for pcc.dist.transport (D-P0-DIST-TRANSPORT).

Covers the transport registry capability taxonomy (only insecure-dev
AVAILABLE, all network modes SKIPPED_WITH_REASON) and the signed-manifest
parser/validator oracle (round-trip, tamper detection, structural validation).
"""
import pytest

from pcc.dist import transport
from pcc.dist.results import STATUS_AVAILABLE, STATUS_SKIPPED, DistUnavailableError


# --- transport registry ----------------------------------------------------
def test_only_insecure_dev_is_available():
    assert transport.probe("insecure-dev").status == STATUS_AVAILABLE
    for mode in ("bonjour", "tcp-ring", "quic", "jaccl-rdma"):
        result = transport.probe(mode)
        assert result.status == STATUS_SKIPPED, mode
        assert result.reason.strip(), mode


def test_registered_modes_cover_research_taxonomy():
    modes = set(transport.registered_modes())
    assert modes == {"insecure-dev", "bonjour", "tcp-ring", "quic", "jaccl-rdma"}


def test_probe_all_returns_one_result_per_mode():
    results = transport.probe_all()
    assert len(results) == len(transport.registered_modes())
    available = [r for r in results if r.available]
    assert len(available) == 1 and available[0].capability == "transport[insecure-dev]"


def test_open_channel_local_only():
    assert transport.open_channel("insecure-dev") == "local-channel:insecure-dev"
    for mode in ("tcp-ring", "quic", "jaccl-rdma", "bonjour"):
        with pytest.raises(DistUnavailableError):
            transport.open_channel(mode)


def test_unknown_transport_is_loud():
    with pytest.raises(transport.ManifestError):
        transport.spec_of("rdma-over-carrier-pigeon")


def test_insecure_dev_marked_not_secure():
    # insecure-dev must never masquerade as authenticated cluster admission.
    assert transport.spec_of("insecure-dev").secure is False


# --- signed manifest oracle -------------------------------------------------
def _nodes(n, mode="tcp-ring"):
    return [{"rank": r, "host": f"mac-{r}.local", "transport": mode} for r in range(n)]


def test_build_manifest_validates_rank_coverage():
    m = transport.build_manifest("cluster-a", _nodes(3))
    assert m.world_size == 3
    assert tuple(node.rank for node in m.nodes) == (0, 1, 2)


def test_build_manifest_rejects_gap_in_ranks():
    bad = [{"rank": 0, "host": "a", "transport": "tcp-ring"},
           {"rank": 2, "host": "b", "transport": "tcp-ring"}]
    with pytest.raises(transport.ManifestError):
        transport.build_manifest("c", bad)


def test_build_manifest_rejects_duplicate_rank():
    bad = [{"rank": 0, "host": "a", "transport": "tcp-ring"},
           {"rank": 0, "host": "b", "transport": "tcp-ring"}]
    with pytest.raises(transport.ManifestError):
        transport.build_manifest("c", bad)


def test_build_manifest_rejects_unknown_transport():
    with pytest.raises(transport.ManifestError):
        transport.build_manifest("c", [{"rank": 0, "host": "a", "transport": "carrier-pigeon"}])


def test_build_manifest_rejects_missing_keys():
    with pytest.raises(transport.ManifestError):
        transport.build_manifest("c", [{"rank": 0, "host": "a"}])  # no transport


def test_sign_and_parse_roundtrip():
    key = b"shared-secret"
    m = transport.build_manifest("cluster-a", _nodes(4, "quic"))
    blob = transport.sign_manifest(m, key)
    parsed = transport.parse_signed_manifest(blob, key)
    assert parsed == m


def test_parse_rejects_wrong_key():
    m = transport.build_manifest("c", _nodes(2))
    blob = transport.sign_manifest(m, b"key-a")
    with pytest.raises(transport.ManifestError):
        transport.parse_signed_manifest(blob, b"key-b")


def test_parse_rejects_tampered_body():
    key = b"k"
    m = transport.build_manifest("c", _nodes(2))
    blob = transport.sign_manifest(m, key)
    sig, _, body = blob.partition(".")
    tampered = sig + "." + body.replace("mac-0", "attacker")
    with pytest.raises(transport.ManifestError):
        transport.parse_signed_manifest(tampered, key)


def test_parse_rejects_malformed_blob():
    with pytest.raises(transport.ManifestError):
        transport.parse_signed_manifest("no-dot-here", b"k")


def test_canonical_body_is_order_independent():
    key = b"k"
    a = transport.build_manifest("c", _nodes(3))
    # same nodes, shuffled input order -> identical signed body
    shuffled = list(reversed(_nodes(3)))
    b = transport.build_manifest("c", shuffled)
    assert transport.sign_manifest(a, key) == transport.sign_manifest(b, key)


def test_require_transport_hard_errors_for_network():
    transport.require_transport("insecure-dev")  # no raise
    with pytest.raises(DistUnavailableError):
        transport.require_transport("tcp-ring")
