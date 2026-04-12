def main() -> None:
    d: dict = {}
    d["x"] = 1
    d["y"] = 2
    d["x"] = 99
    # Order: x, y (re-assigning x preserves its original position)
    for k in d:
        print(k)
        print(d[k])


main()
