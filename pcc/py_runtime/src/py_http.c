#include "py_internal.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef _WIN32
#include <dlfcn.h>
#include <netdb.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
#endif

/* libcurl is loaded at runtime so HTTPS stays a small C-kernel transport
 * primitive and does not become a build-time dependency of every pcc binary. */
#ifndef _WIN32
typedef void PccCurl;
typedef int PccCurlCode;
typedef PccCurl *(*PccCurlEasyInit)(void);
typedef PccCurlCode (*PccCurlEasySetopt)(PccCurl *, int, ...);
typedef PccCurlCode (*PccCurlEasyPerform)(PccCurl *);
typedef void (*PccCurlEasyCleanup)(PccCurl *);

enum {
    PCC_CURLOPT_WRITEDATA = 10001,
    PCC_CURLOPT_URL = 10002,
    PCC_CURLOPT_WRITEFUNCTION = 20011,
    PCC_CURLOPT_USERAGENT = 10018,
    PCC_CURLOPT_TIMEOUT = 13,
    PCC_CURLOPT_FAILONERROR = 45,
    PCC_CURLOPT_FOLLOWLOCATION = 52,
    PCC_CURLOPT_CONNECTTIMEOUT = 78,
    PCC_CURLOPT_NOSIGNAL = 99
};

static size_t pcc_curl_write(void *ptr, size_t size, size_t nmemb, void *stream) {
    return fwrite(ptr, size, nmemb, (FILE *)stream);
}

static void *open_system_libcurl(void) {
#ifdef __APPLE__
    const char *names[] = {
        "/usr/lib/libcurl.4.dylib",
        "libcurl.4.dylib",
        "libcurl.dylib",
        NULL
    };
#else
    const char *names[] = {"libcurl.so.4", "libcurl.so", NULL};
#endif
    for (size_t i = 0; names[i] != NULL; i++) {
        void *handle = dlopen(names[i], RTLD_LAZY | RTLD_LOCAL);
        if (handle != NULL) return handle;
    }
    return NULL;
}

static int download_with_system_libcurl(const char *url, const char *dest) {
    void *library = open_system_libcurl();
    if (library == NULL) return -10;
    PccCurlEasyInit easy_init = (PccCurlEasyInit)dlsym(library, "curl_easy_init");
    PccCurlEasySetopt easy_setopt =
        (PccCurlEasySetopt)dlsym(library, "curl_easy_setopt");
    PccCurlEasyPerform easy_perform =
        (PccCurlEasyPerform)dlsym(library, "curl_easy_perform");
    PccCurlEasyCleanup easy_cleanup =
        (PccCurlEasyCleanup)dlsym(library, "curl_easy_cleanup");
    if (easy_init == NULL || easy_setopt == NULL || easy_perform == NULL
        || easy_cleanup == NULL) {
        dlclose(library);
        return -11;
    }
    FILE *out = fopen(dest, "wb");
    if (out == NULL) {
        dlclose(library);
        return -12;
    }
    PccCurl *curl = easy_init();
    if (curl == NULL) {
        fclose(out);
        dlclose(library);
        return -13;
    }
    int configured = 1;
    configured &= easy_setopt(curl, PCC_CURLOPT_URL, url) == 0;
    configured &= easy_setopt(curl, PCC_CURLOPT_WRITEDATA, out) == 0;
    configured &= easy_setopt(curl, PCC_CURLOPT_WRITEFUNCTION, pcc_curl_write) == 0;
    configured &= easy_setopt(curl, PCC_CURLOPT_USERAGENT, "pcc-owned-acquire/1") == 0;
    configured &= easy_setopt(curl, PCC_CURLOPT_FOLLOWLOCATION, 1L) == 0;
    configured &= easy_setopt(curl, PCC_CURLOPT_FAILONERROR, 1L) == 0;
    configured &= easy_setopt(curl, PCC_CURLOPT_CONNECTTIMEOUT, 20L) == 0;
    configured &= easy_setopt(curl, PCC_CURLOPT_TIMEOUT, 60L) == 0;
    configured &= easy_setopt(curl, PCC_CURLOPT_NOSIGNAL, 1L) == 0;
    PccCurlCode rc = configured ? easy_perform(curl) : -1;
    easy_cleanup(curl);
    fclose(out);
    dlclose(library);
    if (rc != 0) {
        remove(dest);
        return -14;
    }
    return 0;
}
#endif

typedef struct {
    uint32_t state[8];
    uint64_t bit_count;
    unsigned char block[64];
    size_t block_len;
} PccSha256;

static uint32_t sha_rotr(uint32_t value, unsigned shift) {
    return (value >> shift) | (value << (32U - shift));
}

static void sha256_transform(PccSha256 *ctx, const unsigned char block[64]) {
    static const uint32_t k[64] = {
        0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U,
        0x3956c25bU, 0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U,
        0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
        0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U,
        0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
        0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
        0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
        0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U,
        0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
        0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
        0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U,
        0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
        0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U,
        0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
        0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
        0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U
    };
    uint32_t w[64];
    for (size_t i = 0; i < 16; i++) {
        size_t j = i * 4;
        w[i] = ((uint32_t)block[j] << 24) | ((uint32_t)block[j + 1] << 16)
            | ((uint32_t)block[j + 2] << 8) | (uint32_t)block[j + 3];
    }
    for (size_t i = 16; i < 64; i++) {
        uint32_t s0 = sha_rotr(w[i - 15], 7) ^ sha_rotr(w[i - 15], 18)
            ^ (w[i - 15] >> 3);
        uint32_t s1 = sha_rotr(w[i - 2], 17) ^ sha_rotr(w[i - 2], 19)
            ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = ctx->state[0], b = ctx->state[1], c = ctx->state[2];
    uint32_t d = ctx->state[3], e = ctx->state[4], f = ctx->state[5];
    uint32_t g = ctx->state[6], h = ctx->state[7];
    for (size_t i = 0; i < 64; i++) {
        uint32_t s1 = sha_rotr(e, 6) ^ sha_rotr(e, 11) ^ sha_rotr(e, 25);
        uint32_t choice = (e & f) ^ ((~e) & g);
        uint32_t temp1 = h + s1 + choice + k[i] + w[i];
        uint32_t s0 = sha_rotr(a, 2) ^ sha_rotr(a, 13) ^ sha_rotr(a, 22);
        uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        uint32_t temp2 = s0 + majority;
        h = g; g = f; f = e; e = d + temp1;
        d = c; c = b; b = a; a = temp1 + temp2;
    }
    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c;
    ctx->state[3] += d; ctx->state[4] += e; ctx->state[5] += f;
    ctx->state[6] += g; ctx->state[7] += h;
}

static void sha256_init(PccSha256 *ctx) {
    static const uint32_t initial[8] = {
        0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
        0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U
    };
    memcpy(ctx->state, initial, sizeof(initial));
    ctx->bit_count = 0;
    ctx->block_len = 0;
}

static void sha256_update(PccSha256 *ctx, const unsigned char *data, size_t len) {
    ctx->bit_count += (uint64_t)len * 8U;
    while (len > 0) {
        size_t room = 64 - ctx->block_len;
        size_t take = len < room ? len : room;
        memcpy(ctx->block + ctx->block_len, data, take);
        ctx->block_len += take;
        data += take;
        len -= take;
        if (ctx->block_len == 64) {
            sha256_transform(ctx, ctx->block);
            ctx->block_len = 0;
        }
    }
}

static void sha256_final(PccSha256 *ctx, unsigned char out[32]) {
    ctx->block[ctx->block_len++] = 0x80;
    if (ctx->block_len > 56) {
        while (ctx->block_len < 64) ctx->block[ctx->block_len++] = 0;
        sha256_transform(ctx, ctx->block);
        ctx->block_len = 0;
    }
    while (ctx->block_len < 56) ctx->block[ctx->block_len++] = 0;
    for (size_t i = 0; i < 8; i++) {
        ctx->block[63 - i] = (unsigned char)(ctx->bit_count >> (i * 8));
    }
    sha256_transform(ctx, ctx->block);
    for (size_t i = 0; i < 8; i++) {
        out[i * 4] = (unsigned char)(ctx->state[i] >> 24);
        out[i * 4 + 1] = (unsigned char)(ctx->state[i] >> 16);
        out[i * 4 + 2] = (unsigned char)(ctx->state[i] >> 8);
        out[i * 4 + 3] = (unsigned char)ctx->state[i];
    }
}

PyObject *py_sha256_file_hex(PyObject *path_obj) {
    const char *path = py_str_utf8(path_obj);
    FILE *fh = fopen(path, "rb");
    if (fh == NULL) return py_str_new("", 0);
    PccSha256 ctx;
    sha256_init(&ctx);
    unsigned char buffer[32768];
    for (;;) {
        size_t count = fread(buffer, 1, sizeof(buffer), fh);
        if (count > 0) sha256_update(&ctx, buffer, count);
        if (count < sizeof(buffer)) {
            if (ferror(fh)) {
                fclose(fh);
                return py_str_new("", 0);
            }
            break;
        }
    }
    fclose(fh);
    unsigned char digest[32];
    char hex[65];
    static const char digits[] = "0123456789abcdef";
    sha256_final(&ctx, digest);
    for (size_t i = 0; i < 32; i++) {
        hex[i * 2] = digits[digest[i] >> 4];
        hex[i * 2 + 1] = digits[digest[i] & 15];
    }
    hex[64] = '\0';
    return py_str_new(hex, 64);
}

static int parse_http_url(
    const char *url,
    char *host,
    size_t host_cap,
    char *port,
    size_t port_cap,
    char *path,
    size_t path_cap
) {
    const char *prefix = "http://";
    size_t prefix_len = strlen(prefix);
    if (url == NULL || strncmp(url, prefix, prefix_len) != 0) return -1;
    const char *authority = url + prefix_len;
    const char *slash = strchr(authority, '/');
    const char *end = slash != NULL ? slash : url + strlen(url);
    const char *colon = NULL;
    for (const char *p = authority; p < end; p++) {
        if (*p == ':') {
            colon = p;
            break;
        }
    }
    size_t host_len = (size_t)((colon != NULL ? colon : end) - authority);
    if (host_len == 0 || host_len >= host_cap) return -1;
    memcpy(host, authority, host_len);
    host[host_len] = '\0';
    if (colon != NULL) {
        size_t port_len = (size_t)(end - colon - 1);
        if (port_len == 0 || port_len >= port_cap) return -1;
        memcpy(port, colon + 1, port_len);
        port[port_len] = '\0';
    } else {
        if (port_cap < 3) return -1;
        strcpy(port, "80");
    }
    const char *path_src = slash != NULL ? slash : "/";
    size_t path_len = strlen(path_src);
    if (path_len == 0 || path_len >= path_cap) return -1;
    memcpy(path, path_src, path_len + 1);
    return 0;
}

#ifndef _WIN32
static int send_all(int fd, const char *buf, size_t len) {
    size_t off = 0;
    while (off < len) {
        ssize_t n = send(fd, buf + off, len - off, 0);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (n == 0) return -1;
        off += (size_t)n;
    }
    return 0;
}

static int connect_http_socket(const char *host, const char *port) {
    struct addrinfo hints;
    struct addrinfo *result = NULL;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int rc = getaddrinfo(host, port, &hints, &result);
    if (rc != 0) return -1;
    int fd = -1;
    for (struct addrinfo *rp = result; rp != NULL; rp = rp->ai_next) {
        fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
        if (fd < 0) continue;
        if (connect(fd, rp->ai_addr, rp->ai_addrlen) == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(result);
    return fd;
}
#endif

int64_t py_http_download_to_file(PyObject *url_obj, PyObject *dest_obj) {
#ifdef _WIN32
    (void)url_obj;
    (void)dest_obj;
    return -1;
#else
    const char *url = py_str_utf8(url_obj);
    const char *dest = py_str_utf8(dest_obj);
#ifndef _WIN32
    if (strncmp(url, "http://", 7) == 0 || strncmp(url, "https://", 8) == 0) {
        int curl_rc = download_with_system_libcurl(url, dest);
        if (curl_rc == 0 || strncmp(url, "https://", 8) == 0) return curl_rc;
    }
#endif
    char host[512];
    char port[32];
    char path[4096];
    if (parse_http_url(url, host, sizeof(host), port, sizeof(port), path, sizeof(path)) != 0) {
        return -2;
    }

    int fd = connect_http_socket(host, port);
    if (fd < 0) return -3;

    char request[8192];
    int req_len = snprintf(
        request,
        sizeof(request),
        "GET %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\nUser-Agent: pcc/1\r\n\r\n",
        path,
        host
    );
    if (req_len <= 0 || (size_t)req_len >= sizeof(request)) {
        close(fd);
        return -4;
    }
    if (send_all(fd, request, (size_t)req_len) != 0) {
        close(fd);
        return -5;
    }

    FILE *out = fopen(dest, "wb");
    if (out == NULL) {
        close(fd);
        return -6;
    }

    char buf[8192];
    char header[65536];
    size_t header_len = 0;
    int header_done = 0;
    int status_ok = 0;
    for (;;) {
        ssize_t n = recv(fd, buf, sizeof(buf), 0);
        if (n < 0) {
            if (errno == EINTR) continue;
            fclose(out);
            close(fd);
            return -7;
        }
        if (n == 0) break;
        size_t off = 0;
        if (!header_done) {
            while (off < (size_t)n && header_len + 1 < sizeof(header)) {
                header[header_len++] = buf[off++];
                header[header_len] = '\0';
                if (
                    header_len >= 4
                    && header[header_len - 4] == '\r'
                    && header[header_len - 3] == '\n'
                    && header[header_len - 2] == '\r'
                    && header[header_len - 1] == '\n'
                ) {
                    header_done = 1;
                    status_ok = strncmp(header, "HTTP/1.0 200", 12) == 0
                        || strncmp(header, "HTTP/1.1 200", 12) == 0;
                    break;
                }
            }
            if (!header_done) continue;
        }
        if (off < (size_t)n) {
            if (fwrite(buf + off, 1, (size_t)n - off, out) != (size_t)n - off) {
                fclose(out);
                close(fd);
                return -8;
            }
        }
    }
    fclose(out);
    close(fd);
    return status_ok ? 0 : -9;
#endif
}
