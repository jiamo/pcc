def main() -> None:
    squares = [i * i for i in range(5)]
    for x in range(5):
        print(squares[x])
    evens = [i for i in range(10) if i % 2 == 0]
    for x in range(5):
        print(evens[x])


main()
