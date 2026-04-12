def main() -> None:
    d: dict[str, int] = {"a": 1, "b": 2, "c": 3}
    total: int = 0
    for k in d:
        total = total + d[k]
    print(total)


main()
