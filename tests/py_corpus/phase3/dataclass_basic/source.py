from dataclasses import dataclass


@dataclass
class Point:
    x: int
    y: int


def main() -> None:
    p = Point(3, 7)
    print(p.x)
    print(p.y)


main()
