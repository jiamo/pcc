// String search benchmark - Boyer-Moore-Horspool + KMP
// Stresses: string processing, table building, byte comparisons, branch prediction

#include <string.h>

#define TEXT_SIZE 1000000
#define PATTERN_SIZE 32

static char text[TEXT_SIZE];

// Boyer-Moore-Horspool
static int bmh_search(const char *text, int text_len, const char *pattern, int pat_len) {
    int skip[256];
    int i, j;
    int count = 0;

    // Build skip table
    for (i = 0; i < 256; i++) skip[i] = pat_len;
    for (i = 0; i < pat_len - 1; i++)
        skip[(unsigned char)pattern[i]] = pat_len - 1 - i;

    // Search
    i = pat_len - 1;
    while (i < text_len) {
        j = pat_len - 1;
        int k = i;
        while (j >= 0 && text[k] == pattern[j]) {
            j--;
            k--;
        }
        if (j < 0) {
            count++;
            i += skip[(unsigned char)text[i]];
        } else {
            i += skip[(unsigned char)text[i]];
        }
    }
    return count;
}

// KMP (Knuth-Morris-Pratt)
static int kmp_table[PATTERN_SIZE + 1];

static void kmp_build(const char *pattern, int len) {
    int i = 2, j = 0;
    kmp_table[0] = -1;
    if (len > 1) kmp_table[1] = 0;

    while (i < len) {
        if (pattern[i - 1] == pattern[j]) {
            kmp_table[i] = j + 1;
            i++;
            j++;
        } else if (j > 0) {
            j = kmp_table[j];
        } else {
            kmp_table[i] = 0;
            i++;
        }
    }
}

static int kmp_search(const char *text, int text_len, const char *pattern, int pat_len) {
    int count = 0;
    int m = 0, i = 0;

    kmp_build(pattern, pat_len);

    while (m + i < text_len) {
        if (pattern[i] == text[m + i]) {
            i++;
            if (i == pat_len) {
                count++;
                m += i - kmp_table[i - 1];
                i = (kmp_table[i - 1] > 0) ? kmp_table[i - 1] : 0;
            }
        } else {
            if (kmp_table[i] > -1) {
                m += i - kmp_table[i];
                i = kmp_table[i];
            } else {
                m++;
                i = 0;
            }
        }
    }
    return count;
}

int main(void) {
    unsigned int seed = 11235813;
    int i;
    long total = 0;

    // Generate text with some structure
    for (i = 0; i < TEXT_SIZE; i++) {
        seed = seed * 1103515245u + 12345u;
        text[i] = 'a' + (seed >> 16) % 8; // small alphabet for more matches
    }

    // Run multiple pattern searches
    char pattern[PATTERN_SIZE + 1];
    for (int iter = 0; iter < 100; iter++) {
        // Extract pattern from text at different positions
        int start = (iter * 9973) % (TEXT_SIZE - PATTERN_SIZE);
        int pat_len = 8 + (iter % 24);
        memcpy(pattern, &text[start], pat_len);
        pattern[pat_len] = '\0';

        total += bmh_search(text, TEXT_SIZE, pattern, pat_len);
        total += kmp_search(text, TEXT_SIZE, pattern, pat_len);
    }

    return (int)(total % 256);
}
