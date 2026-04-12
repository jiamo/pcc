import json


def main() -> None:
    arr = json.loads("[1, 2, 3, 4, 5]")
    s = 0
    for i in range(5):
        s = s + arr[i]
    print(s)


main()
