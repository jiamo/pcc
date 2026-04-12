import base64


def main() -> None:
    # b64encode takes bytes; we pass a CPython str through builtins.bytes
    import builtins
    raw = builtins.bytes("hello", "ascii")
    enc = base64.b64encode(raw)
    print(enc)


main()
