import os


def main() -> None:
    if os.path.exists("/definitely/not/a/real/path/xyz"):
        print("yes")
    else:
        print("no")


main()
