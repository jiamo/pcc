#include <stdio.h>
#include <string.h>

#include "zlib-1.3.1/zlib.h"

static int fail_zlib(const char *msg, int rc) {
    printf("%s rc=%d\n", msg, rc);
    return 1;
}

int main(void) {
    static const char hello[] = "hello, hello!";
    Bytef compr[256];
    Bytef uncompr[256];
    Bytef deflated[256];
    Bytef inflated[256];
    uLongf compr_len = sizeof(compr);
    uLongf uncompr_len = sizeof(uncompr);
    uLong src_len = (uLong)strlen(hello) + 1;
    z_stream c_stream;
    z_stream d_stream;
    int rc;

    printf(
        "zlib version %s = 0x%04x, compile flags = 0x%lx\n",
        zlibVersion(),
        ZLIB_VERNUM,
        zlibCompileFlags()
    );

    rc = compress(compr, &compr_len, (const Bytef *)hello, src_len);
    if (rc != Z_OK) {
        return fail_zlib("compress failed", rc);
    }

    rc = uncompress(uncompr, &uncompr_len, compr, compr_len);
    if (rc != Z_OK) {
        return fail_zlib("uncompress failed", rc);
    }

    if (strcmp((const char *)uncompr, hello) != 0) {
        return fail_zlib("uncompress content mismatch", -1);
    }
    printf("compress/uncompress: %s\n", (const char *)uncompr);

    memset(&c_stream, 0, sizeof(c_stream));
    c_stream.next_in = (Bytef *)hello;
    c_stream.avail_in = (uInt)src_len;
    c_stream.next_out = deflated;
    c_stream.avail_out = sizeof(deflated);

    rc = deflateInit(&c_stream, Z_DEFAULT_COMPRESSION);
    if (rc != Z_OK) {
        return fail_zlib("deflateInit failed", rc);
    }

    rc = deflate(&c_stream, Z_FINISH);
    if (rc != Z_STREAM_END) {
        deflateEnd(&c_stream);
        return fail_zlib("deflate failed", rc);
    }

    compr_len = c_stream.total_out;
    deflateEnd(&c_stream);

    memset(&d_stream, 0, sizeof(d_stream));
    d_stream.next_in = deflated;
    d_stream.avail_in = (uInt)compr_len;
    d_stream.next_out = inflated;
    d_stream.avail_out = sizeof(inflated);

    rc = inflateInit(&d_stream);
    if (rc != Z_OK) {
        return fail_zlib("inflateInit failed", rc);
    }

    rc = inflate(&d_stream, Z_FINISH);
    if (rc != Z_STREAM_END) {
        inflateEnd(&d_stream);
        return fail_zlib("inflate failed", rc);
    }
    inflateEnd(&d_stream);

    if (strcmp((const char *)inflated, hello) != 0) {
        return fail_zlib("inflate content mismatch", -1);
    }
    printf("deflate/inflate: %s\n", (const char *)inflated);
    printf("OK\n");
    return 0;
}
