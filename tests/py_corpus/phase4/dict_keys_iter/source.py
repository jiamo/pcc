import json


def main() -> None:
    d = json.loads('{"a": 1, "b": 2, "c": 3}')
    total = 0
    for k in d:
        total = total + d[k]
    print(total)


main()
