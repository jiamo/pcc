import json


def main() -> None:
    payload = json.dumps({"a": 1, "b": 2}, sort_keys=True)
    print(payload)


main()
