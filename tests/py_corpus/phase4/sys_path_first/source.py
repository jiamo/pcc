import sys


def main() -> None:
    first = sys.path[0]
    # Just print its length; the actual value varies by install.
    print(len(first) >= 0)


main()
