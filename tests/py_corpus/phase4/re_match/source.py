import re


def main() -> None:
    m = re.match("a+", "aaab")
    if m:
        print("matched")
    else:
        print("no match")


main()
