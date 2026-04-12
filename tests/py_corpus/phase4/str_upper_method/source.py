import builtins


def main() -> None:
    # Use str constructor to force CPython str path.
    s = builtins.str("hello")
    print(s.upper())


main()
