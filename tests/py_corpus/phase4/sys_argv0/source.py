import sys


def main() -> None:
    # len(sys.argv) from a pcc exe is at least 1
    n = len(sys.argv)
    if n >= 1:
        print("argc-ok")
    else:
        print("argc-bad")


main()
