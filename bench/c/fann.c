#include<stdio.h>
int max(int a, int b)
{
    if (a > b){
        return a;
    }
    else{
        return b;
    }
}

int fannkuchredux(int n)
{
    int perm[12];
    int perm1[12];
    int count[12];
    int maxFlipsCount = 0;
    int permCount = 0;
    int checksum = 0;
    int done = 0;

    int i;

    for (i=0; i<n; i+=1)
        perm1[i] = i;
        count[i] = 0;
    int r = n;

    while (done == 0) {
        while (r != 1) {
            count[r-1] = r;
            r -= 1;
        }

        for (i=0; i<n; i+=1)
            perm[i] = perm1[i];
        int flipsCount = 0;
        int k = perm[0];

        while (k != 0) {
            int k2 = (k+1) >> 1;
            for (i=0; i<k2; i++) {
                int temp = perm[i]; perm[i] = perm[k-i]; perm[k-i] = temp;
            }
            flipsCount += 1;
            k = perm[0];
        }
        if (flipsCount > maxFlipsCount){
            maxFlipsCount = flipsCount;
        }

        if (permCount % 2 == 0){
            checksum += flipsCount;
        }
        else{
            checksum -= flipsCount;
        }

        /* Use incremental change to generate another permutation */
        while (1) {
            if (r == n) {

                done = 1;
                break;
            }

            int perm0 = perm1[0];
            i = 0;
            while (i < r) {
                int j = i + 1;
                perm1[i] = perm1[j];
                i = j;
            }
            perm1[r] = perm0;
            count[r] = count[r] - 1;
            if (count[r] > 0) break;
            r++;
        }
        if (done == 0){
            permCount++;
        }
    }
    return maxFlipsCount;
}

int main()
{
    int result = fannkuchredux(11);
    printf("%d\n", result);
    return result;
}