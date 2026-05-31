import base64


def main() -> None:
    # b64encode takes bytes; use bytes literal input for native path.
    raw = b"hello"
    enc = base64.b64encode(raw)
    print(enc)


main()
