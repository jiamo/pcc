def main() -> None:
    d: dict = {}
    d["a"] = 1
    d["b"] = 2
    d["c"] = 3
    del d["a"]
    d["a"] = 99
    # Order after delete+readd: b, c, a
    for k in d:
        print(k)


main()
