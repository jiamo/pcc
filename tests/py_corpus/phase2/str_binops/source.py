def main() -> None:
    indent: int = 3
    s: str = "  " * indent
    print(len(s))
    name: str = "foo"
    greeting: str = "hello, " + name
    print(greeting)
    prefix: str = "=" * 5
    print(prefix)


main()
