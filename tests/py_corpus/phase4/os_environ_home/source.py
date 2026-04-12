import os


def main() -> None:
    home = os.environ["HOME"]
    print(len(home) > 0)


main()
