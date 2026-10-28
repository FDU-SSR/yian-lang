#include <stdint.h>
#include <stdio.h>

typedef struct {
    char symbol;
    double prob;
} AminoAcid;


const int32_t IA = 3877;
const int32_t IC = 29573;
const int32_t IM = 139968;

const char alu[] = "GGCCGGGCGCGGTGGCTCACGCCTGTAATCCCAGCACTTTGGGAGGCCGAGGCGGGCGGATCACCTGAGGTCAGGAGTTCGAGACCAGCCTGGCCAACATGGTGAAACCCCGTCTCTACTAAAAATACAAAAATTAGCCGGGCGTGGTGGCGCGCGCCTGTAATCCCAGCTACTCGGGAGGCTGAGGCAGGAGAATCGCTTGAACCCGGGAGGCGGAGGTTGCAGTGAGCCGAGATCGCGCCACTGCACTCCAGCCTGGGCGACAGAGCGAGACTCCGTCTCAAAAA";

void make_cumulative(AminoAcid* genelist, int32_t count, int64_t* checksum) {
    double p = 0.0;
    for (int32_t i = 0; i < count; ++i) {
        p += genelist[i].prob;
        genelist[i].prob = p;
        *checksum += (int64_t)genelist[i].symbol;
    }
}

void repeat_fasta_compute_only(const char* seq, int32_t n, int64_t* checksum) {
    int32_t seq_len = 287;
    for (int32_t i = 0; i < n; ++i) {
        *checksum += (int64_t)seq[i % seq_len];
    }
}

void random_fasta_compute_only(AminoAcid* genelist, int32_t count, int32_t n, int32_t* g_seed, int64_t* checksum) {
    for (int32_t i = 0; i < n; ++i) {
        *g_seed = (*g_seed * IA + IC) % 139968;
        double r = (double)*g_seed / 139968.0;

        char selected_char = ' ';
        for (int32_t j = 0; j < count; ++j) {
            if (r < genelist[j].prob) {
                selected_char = genelist[j].symbol;
                break;
            }
        }
        *checksum += (int64_t)selected_char;
    }
}

int main() {
    static int32_t g_seed = 42;
    static int64_t checksum;
    static AminoAcid iub[] = {
    {'a', 0.27}, {'c', 0.12}, {'g', 0.12}, {'t', 0.27},
    {'B', 0.02}, {'D', 0.02}, {'H', 0.02}, {'K', 0.02},
    {'M', 0.02}, {'N', 0.02}, {'R', 0.02}, {'S', 0.02},
    {'V', 0.02}, {'W', 0.02}, {'Y', 0.02}
};

    static AminoAcid homosapiens[] = {
    {'a', 0.3029549426680},
    {'c', 0.1979883004921},
    {'g', 0.1979883004921},
    {'t', 0.3010684568318}
};


    int32_t n = 25000000;
    make_cumulative(iub, 15, &checksum);
    make_cumulative(homosapiens, 4, &checksum);
    random_fasta_compute_only(homosapiens, 4, n * 5, &g_seed, &checksum);
    random_fasta_compute_only(iub, 15, n * 3, &g_seed, &checksum);
    repeat_fasta_compute_only(alu, n * 2, &checksum);
    printf("checksum: %d\n", ((int32_t)checksum) % 256);
    return 0;
}
