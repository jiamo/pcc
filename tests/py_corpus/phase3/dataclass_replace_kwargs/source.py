from dataclasses import dataclass, replace


@dataclass
class Point:
    x: int
    y: int


def main() -> None:
    p = Point(1, 2)
    q = replace(p, y=9)
    updates = {"x": 5, "y": 7}
    r = replace(q, **updates)
    print(p.x)
    print(p.y)
    print(q.x)
    print(q.y)
    print(r.x)
    print(r.y)


main()
