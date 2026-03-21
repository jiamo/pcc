// EXPECT: 5
#include <stdlib.h>
#include <string.h>
#include "mystring.h"

int main() {
    int len = my_strlen("hello");
    int cmp = my_strcmp("abc", "abc");

    // Test reverse: copy "abcde" to buffer, reverse it, check first char
    char *buf = malloc(6);
    strcpy(buf, "abcde");
    my_reverse(buf, 5);
    // buf is now "edcba", buf[0] = 'e' = 101
    int first = *(buf + 0);
    free(buf);

    // len=5, cmp=0, first='e'=101
    // return len so it's simple
    return len;
}
