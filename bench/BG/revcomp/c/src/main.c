
/* The Computer Language Benchmarks Game
 * https://salsa.debian.org/benchmarksgame-team/benchmarksgame/

   contributed by Mr Ledrug
   modified by Attila Balazs
*/

#define _GNU_SOURCE
#include <sched.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <ctype.h>
#include <unistd.h>
#include <string.h>

char *pairs = "ATCGGCTAUAMKRYWWSSYRKMVBHDDHBVNN\n\n";
char tbl[256];

void process(char *from, char *to) {
   while (*from++ != '\n');

   size_t len = to - from;
   size_t off = 60 - (len % 61);

   if (off) {
      char *m;
      for (m = from + 60 - off; m < to; m += 61) {
         memmove(m + 1, m, off);
         *m = '\n';
      }
   }

   char c;
   for (to--; from <= to; from++, to--)
      c = tbl[(unsigned char)*from], *from = tbl[(unsigned char)*to], *to = c;
}

int main() {
   char *s;
   for (s = pairs; *s; s += 2) {
      tbl[toupper(s[0])] = s[1];
      tbl[tolower(s[0])] = s[1];
   }


   const size_t _1M = 1024 * 1024;
   size_t buflen = 8 * 1024, len, end = 0;
   char *buf = malloc(buflen);
   if (buf == NULL) {
      perror("malloc");
      return EXIT_FAILURE;
   }

   int in = fileno(stdin);
   while (1) {
      ssize_t amount = read(in, buf + end, buflen - 256 - end);
      if (amount < 0) {
         if (errno == EINTR) continue;
         perror("read");
         free(buf);
         return EXIT_FAILURE;
      }
      if (amount == 0) break;
      len = (size_t)amount;
      end += len;
      if (end >= buflen - 256) {
         buflen = (buflen >= _1M) ? buflen + _1M : buflen * 2;
         char *grown = realloc(buf, buflen);
         if (grown == NULL) {
            perror("realloc");
            free(buf);
            return EXIT_FAILURE;
         }
         buf = grown;
      }
   }
   if (end == 0) {
      free(buf);
      return 0;
   }
   buf[end] = '>';

   char *from, *to = buf + end - 1;
   while (1) {
      for (from = to; *from != '>'; from--);

      process(from, to);

      if (from == buf) break;
      to = from - 1;
   }

   size_t written = 0;
   while (written < end) {
      ssize_t amount = write(fileno(stdout), buf + written, end - written);
      if (amount < 0) {
         if (errno == EINTR) continue;
         perror("write");
         free(buf);
         return EXIT_FAILURE;
      }
      if (amount == 0) {
         fputs("write made no progress\n", stderr);
         free(buf);
         return EXIT_FAILURE;
      }
      written += (size_t)amount;
   }
   free(buf);

   return 0;
}
