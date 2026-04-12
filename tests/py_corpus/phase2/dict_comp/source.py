def main() -> None:
    squares = {i: i * i for i in range(5)}
    for x in range(5):
        print(squares[x])
    even_squares = {i: i * i for i in range(10) if i % 2 == 0}
    for x in range(10):
        if x % 2 == 0:
            print(even_squares[x])


main()
