def pairs_sum() -> list[int]:
    pairs: list[tuple[int, int]] = [(1, 2), (3, 4), (5, 6)]
    out: list[int] = []
    for (a, b) in pairs:
        out.append(a + b)
    return out


def main() -> None:
    xs = pairs_sum()
    for v in xs:
        print(v)


main()
