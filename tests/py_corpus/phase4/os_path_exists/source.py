import os


def main() -> None:
    if os.path.exists("/tmp"):
        print("yes")
    else:
        print("no")


main()
