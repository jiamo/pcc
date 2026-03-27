/*
 * test_pcre_main.c - exercise core libpcre3 functionality.
 *
 * Build with pcc:
 *   uv run pcc --depends-on projects/pcre-8.45=libpcre.la projects/test_pcre_main.c
 * Or with cc:
 *   cc -O0 -DHAVE_CONFIG_H projects/test_pcre_main.c projects/pcre-8.45/pcre_*.c \
 *      -o test_pcre -lm
 *
 * Expected output: a series of "PASS" lines, return 0 on success.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "pcre-8.45/pcre.h"

static int failures = 0;
static int tests = 0;

static void check(int ok, const char *name) {
    tests++;
    if (ok) {
        printf("  PASS: %s\n", name);
    } else {
        printf("  FAIL: %s\n", name);
        failures++;
    }
    fflush(stdout);
}

/* ---------- 1. pcre_version ---------- */
static void test_version(void) {
    const char *v = pcre_version();
    printf("pcre_version: %s\n", v);
    fflush(stdout);
    check(v != NULL && strlen(v) > 0, "version non-empty");
    check(v[0] == '8', "version starts with 8");
}

/* ---------- 2. basic compile + match ---------- */
static void test_basic_match(void) {
    const char *error;
    int erroffset;
    int ovector[30];

    pcre *re = pcre_compile("hello", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile 'hello'");

    int rc = pcre_exec(re, NULL, "say hello world", 15, 0, 0, ovector, 30);
    check(rc >= 0, "match 'hello' in 'say hello world'");
    check(ovector[0] == 4 && ovector[1] == 9, "match offset [4,9)");

    rc = pcre_exec(re, NULL, "goodbye", 7, 0, 0, ovector, 30);
    check(rc < 0, "no match 'hello' in 'goodbye'");

    pcre_free(re);
}

/* ---------- 3. capture groups ---------- */
static void test_capture_groups(void) {
    const char *error;
    int erroffset;
    int ovector[30];

    pcre *re = pcre_compile("(\\w+)@(\\w+\\.\\w+)", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile email pattern");

    const char *subject = "user@example.com";
    int rc = pcre_exec(re, NULL, subject, (int)strlen(subject), 0, 0, ovector, 30);
    check(rc == 3, "3 capture groups (full + 2 subs)");

    /* Check group 1: user */
    int len1 = ovector[3] - ovector[2];
    check(len1 == 4 && strncmp(subject + ovector[2], "user", 4) == 0,
          "group 1 = 'user'");

    /* Check group 2: example.com */
    int len2 = ovector[5] - ovector[4];
    check(len2 == 11 && strncmp(subject + ovector[4], "example.com", 11) == 0,
          "group 2 = 'example.com'");

    pcre_free(re);
}

/* ---------- 4. alternation ---------- */
static void test_alternation(void) {
    const char *error;
    int erroffset;
    int ovector[30];

    pcre *re = pcre_compile("cat|dog|bird", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile alternation");

    check(pcre_exec(re, NULL, "a dog", 5, 0, 0, ovector, 30) >= 0, "match 'dog'");
    check(pcre_exec(re, NULL, "a bird", 6, 0, 0, ovector, 30) >= 0, "match 'bird'");
    check(pcre_exec(re, NULL, "a fish", 6, 0, 0, ovector, 30) < 0, "no match 'fish'");

    pcre_free(re);
}

/* ---------- 5. quantifiers ---------- */
static void test_quantifiers(void) {
    const char *error;
    int erroffset;
    int ovector[30];

    pcre *re = pcre_compile("ab+c", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile 'ab+c'");

    check(pcre_exec(re, NULL, "abc", 3, 0, 0, ovector, 30) >= 0, "match 'abc'");
    check(pcre_exec(re, NULL, "abbbbc", 6, 0, 0, ovector, 30) >= 0, "match 'abbbbc'");
    check(pcre_exec(re, NULL, "ac", 2, 0, 0, ovector, 30) < 0, "no match 'ac'");

    pcre_free(re);

    re = pcre_compile("ab?c", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile 'ab?c'");
    check(pcre_exec(re, NULL, "ac", 2, 0, 0, ovector, 30) >= 0, "match 'ac' for ab?c");
    check(pcre_exec(re, NULL, "abc", 3, 0, 0, ovector, 30) >= 0, "match 'abc' for ab?c");

    pcre_free(re);

    re = pcre_compile("ab*c", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile 'ab*c'");
    check(pcre_exec(re, NULL, "ac", 2, 0, 0, ovector, 30) >= 0, "match 'ac' for ab*c");
    check(pcre_exec(re, NULL, "abbc", 4, 0, 0, ovector, 30) >= 0, "match 'abbc' for ab*c");

    pcre_free(re);
}

/* ---------- 6. character classes ---------- */
static void test_char_classes(void) {
    const char *error;
    int erroffset;
    int ovector[30];

    pcre *re = pcre_compile("[0-9]+", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile '[0-9]+'");

    int rc = pcre_exec(re, NULL, "abc123def", 9, 0, 0, ovector, 30);
    check(rc >= 0, "match digits");
    check(ovector[0] == 3 && ovector[1] == 6, "digits at [3,6)");

    pcre_free(re);

    re = pcre_compile("\\d{3}-\\d{4}", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile phone pattern");
    check(pcre_exec(re, NULL, "call 555-1234 now", 17, 0, 0, ovector, 30) >= 0,
          "match phone number");

    pcre_free(re);
}

/* ---------- 7. anchors ---------- */
static void test_anchors(void) {
    const char *error;
    int erroffset;
    int ovector[30];

    pcre *re = pcre_compile("^hello", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile '^hello'");
    check(pcre_exec(re, NULL, "hello world", 11, 0, 0, ovector, 30) >= 0,
          "match ^hello at start");
    check(pcre_exec(re, NULL, "say hello", 9, 0, 0, ovector, 30) < 0,
          "no match ^hello in middle");
    pcre_free(re);

    re = pcre_compile("world$", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile 'world$'");
    check(pcre_exec(re, NULL, "hello world", 11, 0, 0, ovector, 30) >= 0,
          "match world$ at end");
    check(pcre_exec(re, NULL, "world hello", 11, 0, 0, ovector, 30) < 0,
          "no match world$ not at end");
    pcre_free(re);
}

/* ---------- 8. case insensitive ---------- */
static void test_caseless(void) {
    const char *error;
    int erroffset;
    int ovector[30];

    pcre *re = pcre_compile("hello", PCRE_CASELESS, &error, &erroffset, NULL);
    check(re != NULL, "compile caseless 'hello'");
    check(pcre_exec(re, NULL, "HELLO", 5, 0, 0, ovector, 30) >= 0,
          "caseless match HELLO");
    check(pcre_exec(re, NULL, "HeLLo", 5, 0, 0, ovector, 30) >= 0,
          "caseless match HeLLo");
    pcre_free(re);
}

/* ---------- 9. pcre_study ---------- */
static void test_study(void) {
    const char *error;
    int erroffset;
    int ovector[30];

    pcre *re = pcre_compile("\\b\\w+@\\w+\\.\\w+\\b", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile for study");

    const char *study_error;
    pcre_extra *extra = pcre_study(re, 0, &study_error);
    /* extra can be NULL if study finds nothing to optimize, that's ok */
    check(study_error == NULL, "study no error");

    const char *s = "email: test@foo.bar ok";
    int rc = pcre_exec(re, extra, s, (int)strlen(s), 0, 0, ovector, 30);
    check(rc >= 0, "match with studied pattern");

    if (extra) pcre_free_study(extra);
    pcre_free(re);
}

/* ---------- 10. pcre_fullinfo ---------- */
static void test_fullinfo(void) {
    const char *error;
    int erroffset;

    pcre *re = pcre_compile("(a)(b)(c)", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile for fullinfo");

    int capture_count;
    int rc = pcre_fullinfo(re, NULL, PCRE_INFO_CAPTURECOUNT, &capture_count);
    check(rc == 0, "fullinfo returns 0");
    check(capture_count == 3, "capture count == 3");

    pcre_free(re);
}

/* ---------- 11. copy_substring ---------- */
static void test_copy_substring(void) {
    const char *error;
    int erroffset;
    int ovector[30];

    pcre *re = pcre_compile("(\\w+)-(\\w+)", 0, &error, &erroffset, NULL);
    check(re != NULL, "compile for copy_substring");

    const char *subject = "foo-bar";
    int rc = pcre_exec(re, NULL, subject, 7, 0, 0, ovector, 30);
    check(rc == 3, "match foo-bar");

    char buf[64];
    int len = pcre_copy_substring(subject, ovector, rc, 1, buf, sizeof(buf));
    check(len > 0 && strcmp(buf, "foo") == 0, "substring 1 = 'foo'");

    len = pcre_copy_substring(subject, ovector, rc, 2, buf, sizeof(buf));
    check(len > 0 && strcmp(buf, "bar") == 0, "substring 2 = 'bar'");

    pcre_free(re);
}

/* ---------- 12. compile error handling ---------- */
static void test_compile_error(void) {
    const char *error;
    int erroffset;

    pcre *re = pcre_compile("[unclosed", 0, &error, &erroffset, NULL);
    check(re == NULL, "bad pattern returns NULL");
    check(error != NULL, "error message set");
    check(erroffset > 0, "error offset set");
}

/* ---------- 13. multiline ---------- */
static void test_multiline(void) {
    const char *error;
    int erroffset;
    int ovector[30];

    pcre *re = pcre_compile("^line2", PCRE_MULTILINE, &error, &erroffset, NULL);
    check(re != NULL, "compile multiline");

    const char *s = "line1\nline2\nline3";
    check(pcre_exec(re, NULL, s, (int)strlen(s), 0, 0, ovector, 30) >= 0,
          "multiline match ^line2");

    pcre_free(re);
}

/* ---------- main ---------- */
int main(void) {
    printf("=== PCRE test suite ===\n\n");
    fflush(stdout);

    test_version();
    test_basic_match();
    test_capture_groups();
    test_alternation();
    test_quantifiers();
    test_char_classes();
    test_anchors();
    test_caseless();
    test_study();
    test_fullinfo();
    test_copy_substring();
    test_compile_error();
    test_multiline();

    printf("\n%d/%d tests passed\n", tests - failures, tests);
    fflush(stdout);
    return failures;
}
