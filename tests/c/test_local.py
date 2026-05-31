

def test_locals():
    d1 = locals()
    d2 = locals()
    # Python 3.13+ (PEP 667): locals() returns a fresh snapshot each call,
    # so d2 will contain 'd1' while d1 won't. Just verify both are dicts.
    assert isinstance(d1, dict)
    assert isinstance(d2, dict)
    assert 'd1' in d2