def main() -> None:
    mod3 = {i % 3 for i in range(10)}
    print(len(mod3))
    evens = {i for i in range(20) if i % 2 == 0}
    print(len(evens))


main()
