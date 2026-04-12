import json


def main() -> None:
    data = json.loads('{"name": "alice", "age": 30}')
    print(data["name"])
    print(data["age"])


main()
