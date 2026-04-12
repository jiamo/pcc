import sys


def main() -> None:
    major = sys.version_info.major
    if major >= 3:
        print("py3")
    else:
        print("py2")


main()
