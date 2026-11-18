#include <assert.h>
#include <stdlib.h>

static int mandelbrot(int size) {
    int sum = 0;
    int byte_acc = 0;
    int bit_num = 0;

    for (int y = 0; y < size; ++y) {
        double ci = (2.0 * y / size) - 1.0;
        for (int x = 0; x < size; ++x) {
            double zrzr = 0.0;
            double zi = 0.0;
            double zizi = 0.0;
            double cr = (2.0 * x / size) - 1.5;

            int z = 0;
            int not_done = 1;
            int escape = 0;
            while (not_done && z < 50) {
                double zr = zrzr - zizi + cr;
                zi = 2.0 * zr * zi + ci;
                zrzr = zr * zr;
                zizi = zi * zi;
                if (zrzr + zizi > 4.0) {
                    not_done = 0;
                    escape = 1;
                }
                z += 1;
            }

            byte_acc = (byte_acc * 2) + escape;
            bit_num += 1;
            if (bit_num == 8) {
                sum ^= byte_acc;
                byte_acc = 0;
                bit_num = 0;
            } else if (x == size - 1) {
                byte_acc <<= (8 - bit_num);
                sum ^= byte_acc;
                byte_acc = 0;
                bit_num = 0;
            }
        }
    }
    return sum;
}

int main(int argc, char **argv) {
    int iterations = argc > 1 ? atoi(argv[1]) : 69;
    for (int i = 0; i < iterations; ++i) {
        assert(mandelbrot(500) == 191 && "mand: checksum is not correct");
    }
    return 0;
}
