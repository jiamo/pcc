def main() -> None:
    text: str = " a  b\tc\n"
    xs = text.split()
    print(len(xs))
    for x in xs:
        print(x)
    ys = "a,,b,".split(",")
    print(len(ys))
    print(ys[0])
    print(ys[1] == "")
    print(ys[2])
    print(ys[3] == "")


main()
