struct Point {
    int x;
    int y;
};

int main() {
    struct Point p;
    p.x = 3;
    p.y = 4;
    int distance_squared = p.x * p.x + p.y * p.y;
    return distance_squared;
}
