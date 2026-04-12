def main() -> None:
    s: str = "  Hello, World!  "
    print(s.strip())
    print(s.strip().upper())
    print(s.strip().lower())
    print("-".join(["a", "b", "c"]))
    print("abc".startswith("a"))
    print("abc".endswith("z"))
    print("hello world".replace("world", "pcc"))
    print("abc".find("b"))


main()
