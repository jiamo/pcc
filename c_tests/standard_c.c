// EXPECT: 0
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <ctype.h>

#define BUF_SIZE 32
#define SUCCESS 0

int main() {
    // String operations
    char *buf = malloc(BUF_SIZE);
    strcpy(buf, "Hello");
    strcat(buf, " World");
    int len = strlen(buf);

    // Math
    int sq = (int)sqrt(144.0);

    // Ctype
    int upper = toupper(97);   // 'a' -> 'A' = 65

    // Verify
    int ok = (len == 11 && sq == 12 && upper == 65);
    free(buf);

    if (ok) return SUCCESS;
    return 1;
}
