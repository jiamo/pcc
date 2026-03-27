#include <stdio.h>
#include <string.h>

#include "zstd-1.5.6/lib/zstd.h"

static int fail_zstd(const char *msg, size_t rc) {
    printf("%s: %s\n", msg, ZSTD_getErrorName(rc));
    return 1;
}

int main(void) {
    static const char hello[] = "hello, zstd!";
    char compressed[256];
    char decompressed[256];
    size_t hello_len = strlen(hello) + 1;
    size_t compressed_size;
    size_t decompressed_size;

    printf(
        "zstd version %s (%u)\n",
        ZSTD_versionString(),
        (unsigned)ZSTD_versionNumber()
    );

    compressed_size = ZSTD_compress(
        compressed,
        sizeof(compressed),
        hello,
        hello_len,
        1
    );
    if (ZSTD_isError(compressed_size)) {
        return fail_zstd("compress failed", compressed_size);
    }

    decompressed_size = ZSTD_decompress(
        decompressed,
        sizeof(decompressed),
        compressed,
        compressed_size
    );
    if (ZSTD_isError(decompressed_size)) {
        return fail_zstd("decompress failed", decompressed_size);
    }

    if (decompressed_size != hello_len) {
        printf("unexpected decompressed size: %u\n", (unsigned)decompressed_size);
        return 1;
    }

    if (strcmp(decompressed, hello) != 0) {
        printf("roundtrip mismatch: %s\n", decompressed);
        return 1;
    }

    printf("roundtrip: %s\n", decompressed);
    printf("OK\n");
    return 0;
}
