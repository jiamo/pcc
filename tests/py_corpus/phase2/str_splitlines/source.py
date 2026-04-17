def main() -> None:
    text: str = "a\nb\r\nc\rd\n"
    xs = text.splitlines()
    print(len(xs))
    for x in xs:
        print(x)
    ys = text.splitlines(keepends=True)
    print(len(ys))
    print(ys[0] == "a\n")
    print(ys[1] == "b\r\n")
    print(ys[2] == "c\r")
    print(ys[3] == "d\n")


main()
