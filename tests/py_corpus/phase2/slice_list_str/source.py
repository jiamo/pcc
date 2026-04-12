def main() -> None:
    xs: list[int] = [10, 20, 30, 40, 50]
    s = xs[1:4]
    for v in s:
        print(v)
    text: str = "hello world"
    print(text[6:])
    print(text[:5])


main()
