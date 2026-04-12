import pathlib


def main() -> None:
    p = pathlib.PurePath("/tmp/foo/bar.txt")
    print(p.name)
    print(p.suffix)


main()
