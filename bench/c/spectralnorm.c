#include<stdio.h>
#include<math.h>
#define N 8000

int A(int i, int j) {
    return ((i + j) * (i + j + 1) / 2) + i + 1;
}


double dot(double v[], double w[], int n) {
    double sum = 0.0;
    for (int i = 0; i < n; i++) {
        sum += v[i] * w[i];
    }
    return sum;
}

void mult_Av(double v[], double out[], int n) {
    for (int i = 0; i < n; i++) {
        double sum = 0.0;
        for (int j = 0; j < n; j++) {
            sum += v[j] / (double)A(i, j);
        }
        out[i] = sum;
    }
}


void mult_Atv(double v[], double out[], int n) {
    for (int i = 0; i < n; i++) {
        double sum = 0.0;
        for (int j = 0; j < n; j++) {
            sum += v[j] / (double)A(j, i);
        }
        out[i] = sum;
    }
}

void mult_AtAv(double v[], double out[], int n, double tmp[]) {
    mult_Av(v, tmp, n);
    mult_Atv(tmp, out, n);
}

void normalize(double v[], int n) {
    double norm = 0.0;
    for (int i = 0; i < n; i++) {
        norm += v[i] * v[i];
    }
    norm = 1.0 / sqrt(norm);
    for (int i = 0; i < n; i++) {
        v[i] *= norm;
    }
}

int main() {
    int n = N;
    static double u[N];
    static double v[N];
    static double tmp[N];


    for (int i = 0; i < n; i++) {
        u[i] = 1.0;
    }


    for (int i = 0; i < 10; i++) {
        mult_AtAv(u, v, n, tmp);
        mult_AtAv(v, u, n, tmp);

        if (i % 2 == 0) {
            normalize(u, n);
        }
    }


    mult_AtAv(u, v, n, tmp);
    double dot_uv = dot(u, v, n);
    double dot_vv = dot(v, v, n);
    double spectral_norm = sqrt(dot_uv / dot_vv);
    int res = (int)(spectral_norm * 100);
    printf("%d\n", res % 256);
    return 0;
}