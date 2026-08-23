#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main() {
    char input[387] = ">ONE Homo sapiens alu\nGGCCGGGCGCGGTGGCTCACGCCTGTAATCCCAGCACTTTGG\n"
                        "GAGGCCGAGGCGGGCGGATCACCTGAGGTCAGGAGTTCGAGA\n"
                        "CCAGCCTGGCCAACATGGTGAAACCCCGTCTCTACTAAAAAT\n"
                        "ACAAAAATTAGCCGGGCGTGGTGGCGGGCGCCTGTAATCCCA\n"
                        ">TWO IUB ambiguity codes\n"
                        "GCTACTCGGGAGGCTGAGGCAGGAGAATCGCTTGAACCCGGG\n"
                        "AGGCGGAGGTTGCAGTGAGCCGAGATCGCGCCACTGCACTCC\n"
                        "AGCCTGGGCGACAGAGCGAGACTCCGTCTCAAAAA\n"
                        ">THREE Homo sapiens frequency\n"
                        "TTT\nAAA\nGGG\nCCC\n";

    char tbl[400] = {0};
    char* pairs = "ATCGGCTAUAMKRYWWSSYRKMVBHDDHBVNN";
    for (int i = 0; pairs[i] != '\0'; i += 2) {
        tbl[(unsigned char)pairs[i]] = pairs[i + 1];
    }

    const int end = 386;
    long long checksum = 0;


    char temp_buf[400] = {0};

    for (unsigned int iteration = 0; iteration < 2000000; ++iteration) {
        int in_offset = 0;


        while (in_offset < end && input[in_offset] != '>') {
            in_offset++;
        }


        while (in_offset < end) {
            while (in_offset < end && input[in_offset] != '\n') {
                in_offset++;
            }
            if (in_offset < end) {
                in_offset++;
            }


            int seq_start = in_offset;
            while (in_offset < end && input[in_offset] != '>') {
                in_offset++;
            }
            int seq_end = in_offset;


            int clean_len = 0;
            for (int i = seq_start; i < seq_end; ++i) {
                if (input[i] != '\n') {
                    temp_buf[clean_len++] = input[i];
                }
            }


            int left = 0, right = clean_len - 1;
            while (left < right) {
                char left_char = tbl[(unsigned char)temp_buf[left]] ? tbl[(unsigned char)temp_buf[left]] : temp_buf[left];
                char right_char = tbl[(unsigned char)temp_buf[right]] ? tbl[(unsigned char)temp_buf[right]] : temp_buf[right];

                temp_buf[left] = right_char;
                temp_buf[right] = left_char;

                checksum += (long long)left_char + (long long)right_char;
                left++;
                right--;
            }


            if (clean_len % 2 == 1) {
                int mid = clean_len / 2;
                if (tbl[(unsigned char)temp_buf[mid]]) {
                    temp_buf[mid] = tbl[(unsigned char)temp_buf[mid]];
                }
                checksum += (long long)temp_buf[mid];
            }


            int newlines = clean_len > 0 ? (clean_len - 1) / 60 : 0;
            checksum += (long long)newlines * (int)'\n';


            while (in_offset < end && input[in_offset] != '>') {
                in_offset++;
            }
        }
    }

    printf("Checksum: %lld\n", checksum % 256);
    return 0;
}