#include <stdio.h>
#include <string.h>

#include "lz4-1.10.0/lib/lz4.h"
#include "lz4-1.10.0/lib/lz4frame.h"
#include "lz4-1.10.0/lib/lz4hc.h"

static int fail_lz4(const char *msg, int code) {
    printf("%s: %d\n", msg, code);
    return 1;
}

static int fail_lz4f(const char *msg, size_t code) {
    printf("%s: %s\n", msg, LZ4F_getErrorName(code));
    return 1;
}

int main(void) {
    static const char hello[] = "hello, lz4!";
    char compressed[256];
    char compressed_hc[256];
    char decompressed[256];
    char frame[256];
    char frame_out[256];
    int hello_len = (int)(strlen(hello) + 1);
    int compressed_size;
    int compressed_hc_size;
    int decompressed_size;
    LZ4F_preferences_t prefs = {0};
    size_t frame_size;
    LZ4F_dctx *dctx = 0;
    size_t src_size;
    size_t dst_size;
    size_t hint;

    printf("lz4 version %s (%d)\n", LZ4_versionString(), LZ4_versionNumber());

    compressed_size = LZ4_compress_default(
        hello,
        compressed,
        hello_len,
        (int)sizeof(compressed)
    );
    if (compressed_size <= 0)
        return fail_lz4("compress_default failed", compressed_size);

    compressed_hc_size = LZ4_compress_HC(
        hello,
        compressed_hc,
        hello_len,
        (int)sizeof(compressed_hc),
        LZ4HC_CLEVEL_DEFAULT
    );
    if (compressed_hc_size <= 0)
        return fail_lz4("compress_HC failed", compressed_hc_size);

    decompressed_size = LZ4_decompress_safe(
        compressed,
        decompressed,
        compressed_size,
        (int)sizeof(decompressed)
    );
    if (decompressed_size != hello_len)
        return fail_lz4("decompress_safe failed", decompressed_size);
    if (strcmp(decompressed, hello) != 0) {
        printf("block roundtrip mismatch: %s\n", decompressed);
        return 1;
    }

    frame_size = LZ4F_compressFrame(
        frame,
        sizeof(frame),
        hello,
        hello_len,
        &prefs
    );
    if (LZ4F_isError(frame_size))
        return fail_lz4f("compressFrame failed", frame_size);

    if (LZ4F_createDecompressionContext(&dctx, LZ4F_VERSION) != 0)
        return 1;
    src_size = frame_size;
    dst_size = sizeof(frame_out);
    hint = LZ4F_decompress(
        dctx,
        frame_out,
        &dst_size,
        frame,
        &src_size,
        0
    );
    LZ4F_freeDecompressionContext(dctx);
    if (LZ4F_isError(hint))
        return fail_lz4f("decompress failed", hint);
    if (hint != 0) {
        printf("frame decompress incomplete: %u\n", (unsigned)hint);
        return 1;
    }
    if ((int)dst_size != hello_len) {
        printf("unexpected frame size: %u\n", (unsigned)dst_size);
        return 1;
    }
    if (strcmp(frame_out, hello) != 0) {
        printf("frame roundtrip mismatch: %s\n", frame_out);
        return 1;
    }

    printf("block roundtrip: %s\n", decompressed);
    printf("hc compressed size: %d\n", compressed_hc_size);
    printf("frame roundtrip: %s\n", frame_out);
    printf("OK\n");
    return 0;
}
