#include "mystring.h"

int my_strlen(char *s) {
    int n = 0;
    while (*(s + n) != 0) n++;
    return n;
}

int my_strcmp(char *a, char *b) {
    int i = 0;
    while (*(a + i) != 0 && *(b + i) != 0) {
        if (*(a + i) != *(b + i))
            return *(a + i) - *(b + i);
        i++;
    }
    return *(a + i) - *(b + i);
}

void my_reverse(char *s, int len) {
    int i;
    int j;
    for (i = 0, j = len - 1; i < j; i++, j--) {
        char t = *(s + i);
        *(s + i) = *(s + j);
        *(s + j) = t;
    }
}
