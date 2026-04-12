def main() -> None:
    xs = [1, 2, 3]
    ys = [10, 20, 30, 40]
    for a, b in zip(xs, ys):
        print(a + b)

    names = ["alice", "bob"]
    ages = [30, 25]
    for name, age in zip(names, ages):
        print(name)
        print(age)


main()
