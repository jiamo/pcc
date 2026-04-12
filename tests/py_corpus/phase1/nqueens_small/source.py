def abs_val(x: int) -> int:
    if x < 0:
        return -x
    return x


def safe(queens: list[int], row: int, col: int) -> bool:
    i: int = 0
    while i < row:
        qi: int = queens[i]
        if qi == col:
            return False
        if abs_val(qi - col) == abs_val(i - row):
            return False
        i = i + 1
    return True


def solve(queens: list[int], row: int, n: int) -> int:
    if row == n:
        return 1
    count: int = 0
    col: int = 0
    while col < n:
        if safe(queens, row, col):
            queens[row] = col
            count = count + solve(queens, row + 1, n)
        col = col + 1
    return count


def nqueens(n: int) -> int:
    queens: list[int] = [0, 0, 0, 0]
    return solve(queens, 0, n)


def main() -> None:
    print(nqueens(4))


main()
