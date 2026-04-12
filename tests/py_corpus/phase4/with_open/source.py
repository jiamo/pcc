import io


def main() -> None:
    with io.StringIO() as buf:
        buf.write("hello")
        print(buf.getvalue())


main()
