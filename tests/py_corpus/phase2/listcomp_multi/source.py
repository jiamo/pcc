def main() -> None:
    flat = [i * 10 + j for i in range(3) for j in range(4)]
    for k in range(12):
        print(flat[k])
    upper_triangle = [(i, j) for i in range(4) for j in range(4) if i < j]
    print(len(upper_triangle))


main()
