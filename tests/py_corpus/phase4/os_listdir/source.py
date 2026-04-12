import os


def main() -> None:
    entries = os.listdir("/")
    n = 0
    for e in entries:
        n = n + 1
    if n > 0:
        print("nonempty")
    else:
        print("empty")


main()
