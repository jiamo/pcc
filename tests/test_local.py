

def test_locals():
    d1 = locals()
    d2 = locals()
    assert d1 is d2