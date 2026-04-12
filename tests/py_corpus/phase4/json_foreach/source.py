import json


def main() -> None:
    arr = json.loads("[10, 20, 30]")
    s = 0
    for x in arr:
        s = s + x
    print(s)


main()
