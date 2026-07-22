from pcc.backend.self_backend_cache_identity import (
    self_backend_emitter_source_identity,
)


def test_emitter_identity_tracks_only_self_backend_implementation(tmp_path):
    backend_dir = tmp_path / "pcc" / "backend"
    backend_dir.mkdir(parents=True)
    emitter = backend_dir / "self_backend_emit.py"
    emitter.write_text("EMITTER = 1\n", encoding="utf-8")
    identity_helper = backend_dir / "self_backend_cache_identity.py"
    identity_helper.write_text("HELPER = 1\n", encoding="utf-8")
    unrelated = backend_dir / "package_backend.py"
    unrelated.write_text("PACKAGE = 1\n", encoding="utf-8")

    first = self_backend_emitter_source_identity(tmp_path)
    unrelated.write_text("PACKAGE = 2\n", encoding="utf-8")
    identity_helper.write_text("HELPER = 2\n", encoding="utf-8")
    unrelated_change = self_backend_emitter_source_identity(tmp_path)
    emitter.write_text("EMITTER = 2\n", encoding="utf-8")
    emitter_change = self_backend_emitter_source_identity(tmp_path)

    assert unrelated_change == first
    assert emitter_change != first
