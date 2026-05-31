#include "py_internal.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef _WIN32
#include <netdb.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
#endif

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
