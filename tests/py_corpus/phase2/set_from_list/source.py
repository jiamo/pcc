def main() -> None:
    lst = [3, 1, 2, 1, 3, 2]
    s = set(lst)
    # convert back to sorted list for deterministic output
    out = sorted(s)
    print(out)
    print(len(s))


main()
