class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def dist(self):
        return self.x * self.x + self.y * self.y

    def __repr__(self):
        return "Point(" + str(self.x) + ", " + str(self.y) + ")"


class Point3(Point):
    def __init__(self, x, y, z):
        Point.__init__(self, x, y)
        self.z = z

    def dist(self):
        return Point.dist(self) + self.z * self.z


p = Point(3, 4)
print(p.x, p.y, p.dist())
print(p)

q = Point3(1, 2, 2)
print(q.x, q.y, q.z, q.dist())
print(isinstance(q, Point), isinstance(p, Point3))
