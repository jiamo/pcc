// Mandelbrot set computation
// From the Computer Language Benchmarks Game
// Stresses: floating point multiply/add, branch prediction, loops

int main(void) {
    int W = 2000, H = 2000;
    int total = 0;
    int y, x;

    for (y = 0; y < H; y++) {
        for (x = 0; x < W; x++) {
            double cr = 2.0 * x / W - 1.5;
            double ci = 2.0 * y / H - 1.0;
            double zr = 0.0, zi = 0.0;
            int i;
            int inside = 1;

            for (i = 0; i < 50; i++) {
                double tr = zr * zr - zi * zi + cr;
                double ti = 2.0 * zr * zi + ci;
                zr = tr;
                zi = ti;
                if (zr * zr + zi * zi > 4.0) {
                    inside = 0;
                    break;
                }
            }
            total += inside;
        }
    }

    // total counts pixels inside the Mandelbrot set
    return total % 256;
}
