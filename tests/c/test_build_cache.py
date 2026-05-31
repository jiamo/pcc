from pcc.build_cache import compute_cache_key, explain_cache_miss


def test_content_change_changes_key(tmp_path):
    src = tmp_path / "x.c"
    src.write_text("int x;\n")
    k1 = compute_cache_key([str(src)])
    src.write_text("int y;\n")
    k2 = compute_cache_key([str(src)])
    assert "content digest changed" in explain_cache_miss(k1, k2)
