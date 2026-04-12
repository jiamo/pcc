import json


def main() -> None:
    data = json.loads('[10, 20, 30]')
    total = 0
    n = len(data)
    i = 0
    while i < n:
        total = total + data[i]
        i = i + 1
    print(total)


main()
