def step(positions: list[int], velocities: list[int]) -> None:
    # Simple 3-body: each body attracted to centroid by a unit kick, then drift.
    n: int = 3
    # Compute centroid.
    total: int = 0
    i: int = 0
    while i < n:
        total = total + positions[i]
        i = i + 1
    c: int = total // n
    # Kick velocities toward centroid.
    i = 0
    while i < n:
        p: int = positions[i]
        if p < c:
            velocities[i] = velocities[i] + 1
        elif p > c:
            velocities[i] = velocities[i] - 1
        i = i + 1
    # Drift positions.
    i = 0
    while i < n:
        positions[i] = positions[i] + velocities[i]
        i = i + 1


def run(iterations: int) -> int:
    positions: list[int] = [0, 10, 20]
    velocities: list[int] = [0, 0, 0]
    t: int = 0
    while t < iterations:
        step(positions, velocities)
        t = t + 1
    total: int = 0
    i: int = 0
    while i < 3:
        total = total + positions[i]
        i = i + 1
    return total


def main() -> None:
    print(run(10))


main()
