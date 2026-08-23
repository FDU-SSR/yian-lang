#include<stdio.h>
int main()
{
    const int w = 8000;
    const int h = 8000;
    int bit_num = 0;
    int byte_acc = 0;
    const int iter = 50;
    const double limit_sq = 4.0;
    int checksum = 0;


    for (int y = 0; y < h; ++y)
    {

        for (int x = 0; x < w; ++x)
        {

            double Zr = 0.0;
            double Zi = 0.0;
            double Tr = 0.0;
            double Ti = 0.0;


            double Cr = 2.0 * x / w - 1.5;
            double Ci = 2.0 * y / h - 1.0;


            for (int i = 0; i < iter; ++i)
            {

                if (Tr + Ti > limit_sq) {
                    break;
                }


                double new_Zi = 2.0 * Zr * Zi + Ci;
                double new_Zr = Tr - Ti + Cr;


                Zr = new_Zr;
                Zi = new_Zi;


                Tr = Zr * Zr;
                Ti = Zi * Zi;
            }


            int is_member = (Tr + Ti <= limit_sq);


            byte_acc = (byte_acc * 2) % 256;
            if (is_member) {
                byte_acc |= 1;
            }
            bit_num++;


            if (bit_num == 8) {
                checksum ^= byte_acc;
                byte_acc = 0;
                bit_num = 0;
            }
        }


        if (bit_num != 0) {
            int remaining_bits = 8 - bit_num;
            int shift = 1;
            for (int j = 0; j < remaining_bits; ++j) {
                shift *= 2;
            }
            byte_acc = (byte_acc * shift) % 256;
            checksum ^= byte_acc;
            byte_acc = 0;
            bit_num = 0;
        }
    }
    printf("%d\n", checksum);
    return 0;
}