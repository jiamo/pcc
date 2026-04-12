import os


def main() -> None:
    home = os.path
    name = home.basename("/tmp/hello.txt")
    print(name)


main()
