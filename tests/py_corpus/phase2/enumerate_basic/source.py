def main() -> None:
    xs: list[int] = [10, 20, 30]
    for (i, x) in enumerate(xs):
        print(i)
        print(x)
    words: list[str] = ["a", "bb", "ccc"]
    for (k, w) in enumerate(words):
        print(k)
        print(w)


main()
