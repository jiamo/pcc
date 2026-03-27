#include <stdio.h>
#include <string.h>

#include <openssl/opensslv.h>
#include <openssl/sha.h>

int main(void) {
    static const unsigned char hello[] = "hello";
    static const char expected[] =
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
    static const char hexchars[] = "0123456789abcdef";
    SHA256_CTX ctx;
    unsigned char digest[SHA256_DIGEST_LENGTH];
    char hex[SHA256_DIGEST_LENGTH * 2 + 1];
    size_t i;

    printf("openssl version %s\n", OPENSSL_VERSION_TEXT);

    if (!SHA256_Init(&ctx))
        return 1;
    if (!SHA256_Update(&ctx, hello, strlen((const char *)hello)))
        return 2;
    if (!SHA256_Final(digest, &ctx))
        return 3;

    for (i = 0; i < SHA256_DIGEST_LENGTH; i++) {
        hex[i * 2] = hexchars[(digest[i] >> 4) & 0x0f];
        hex[i * 2 + 1] = hexchars[digest[i] & 0x0f];
    }
    hex[sizeof(hex) - 1] = '\0';

    printf("sha256: %s\n", hex);
    if (strcmp(hex, expected) != 0) {
        printf("sha256 mismatch\n");
        return 4;
    }

    printf("OK\n");
    return 0;
}
