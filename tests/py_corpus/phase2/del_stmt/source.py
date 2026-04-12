def main() -> None:
    d: dict[str, int] = {"a": 1, "b": 2, "c": 3}
    print(len(d))
    del d["b"]
    print(len(d))


main()
