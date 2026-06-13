#include "py_internal.h"
#include "py_io_waitset.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef _WIN32
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <fcntl.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
#endif

#ifndef _WIN32
static void asyncio_raise_oserror(const char *msg) {
    py_raise(py_exc_new(PY_EXC_OSERROR, msg));
}

static const char *asyncio_host_cstr(PyObject *host) {
    if (host == NULL || host == py_None) return NULL;
    if (PY_IS_TAGGED_INT(host)) return NULL;
    if (py_type_of(host) != PY_TYPE_STR) return NULL;
    const char *text = py_str_utf8(host);
    if (text == NULL || text[0] == '\0') return NULL;
    return text;
}

static int asyncio_port_cstr(PyObject *port, char *buf, size_t cap) {
    if (buf == NULL || cap == 0) return -1;
    if (port == NULL || port == py_None) {
        snprintf(buf, cap, "0");
        return 0;
    }
    if (!PY_IS_TAGGED_INT(port) && py_type_of(port) == PY_TYPE_STR) {
        const char *text = py_str_utf8(port);
        if (text == NULL || text[0] == '\0' || strlen(text) >= cap) return -1;
        strcpy(buf, text);
        return 0;
    }
    int64_t value = py_int_value_i64(port);
    if (value < 0 || value > 65535) return -1;
    snprintf(buf, cap, "%lld", (long long)value);
    return 0;
}

static void asyncio_prepare_socket(int fd) {
#ifdef SO_NOSIGPIPE
    int one = 1;
    (void)setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));
#endif
}

static int asyncio_set_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) return -1;
    if ((flags & O_NONBLOCK) != 0) return 0;
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static int asyncio_set_blocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) return -1;
    if ((flags & O_NONBLOCK) == 0) return 0;
    return fcntl(fd, F_SETFL, flags & ~O_NONBLOCK);
}

static int64_t asyncio_fd_value(PyObject *fd_obj) {
    if (fd_obj == NULL || fd_obj == py_None) return -1;
    return py_int_value_i64(fd_obj);
}

static int asyncio_send_all_raw(int fd, const char *data, size_t n) {
    size_t sent = 0;
    while (sent < n) {
        ssize_t rc = send(fd, data + sent, n - sent, 0);
        if (rc < 0) {
            if (errno == EINTR) continue;
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                fd_set wfds;
                FD_ZERO(&wfds);
                FD_SET(fd, &wfds);
                if (select(fd + 1, NULL, &wfds, NULL, NULL) < 0 && errno != EINTR) {
                    return -1;
                }
                continue;
            }
            return -1;
        }
        if (rc == 0) return -1;
        sent += (size_t)rc;
    }
    return 0;
}

static int64_t g_asyncio_relay_step_last_progress = 0;

#define PCC_ASYNCIO_RELAY_BUF_SIZE 65536
#define PCC_ASYNCIO_RELAY_DRAIN_CHUNKS 32

static int asyncio_fd_readable_now(int fd) {
    fd_set rfds;
    FD_ZERO(&rfds);
    FD_SET(fd, &rfds);
    struct timeval tv;
    tv.tv_sec = 0;
    tv.tv_usec = 0;
    int rc = select(fd + 1, &rfds, NULL, NULL, &tv);
    if (rc < 0) {
        if (errno == EINTR) return 0;
        return -1;
    }
    return rc > 0 && FD_ISSET(fd, &rfds);
}

static void asyncio_relay_drain_direction(
    int fd_in,
    int fd_out,
    int *active,
    char *buf,
    size_t buf_size,
    int *made_progress
) {
    int chunks = 0;
    while (*active && chunks < PCC_ASYNCIO_RELAY_DRAIN_CHUNKS) {
        if (chunks > 0) {
            int ready = asyncio_fd_readable_now(fd_in);
            if (ready <= 0) {
                if (ready < 0) {
                    *active = 0;
                    shutdown(fd_out, SHUT_WR);
                }
                break;
            }
        }
        ssize_t n = recv(fd_in, buf, buf_size, 0);
        if (n > 0) {
            *made_progress = 1;
            chunks++;
            if (asyncio_send_all_raw(fd_out, buf, (size_t)n) != 0) {
                *active = 0;
                shutdown(fd_in, SHUT_RD);
                shutdown(fd_out, SHUT_WR);
                break;
            }
        } else if (n == 0) {
            *active = 0;
            shutdown(fd_out, SHUT_WR);
            break;
        } else if (errno == EINTR) {
            continue;
        } else if (errno == EAGAIN || errno == EWOULDBLOCK) {
            break;
        } else {
            *active = 0;
            shutdown(fd_out, SHUT_WR);
            break;
        }
    }
}

static void asyncio_close_unique4(int fd1, int fd2, int fd3, int fd4) {
    int fds[4] = {fd1, fd2, fd3, fd4};
    for (int i = 0; i < 4; i++) {
        int fd = fds[i];
        if (fd < 0) continue;
        int seen = 0;
        for (int j = 0; j < i; j++) {
            if (fds[j] == fd) {
                seen = 1;
                break;
            }
        }
        if (!seen) close(fd);
    }
}

static int asyncio_socket_connect(const char *host, const char *port) {
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
        asyncio_prepare_socket(fd);
        if (connect(fd, rp->ai_addr, rp->ai_addrlen) == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(result);
    return fd;
}

static const char *asyncio_bytes_data(PyObject *o, int64_t *n) {
    if (n == NULL) return NULL;
    *n = 0;
    if (o == NULL || PY_IS_TAGGED_INT(o)) return NULL;
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_BYTES) {
        PyBytesObject *b = (PyBytesObject *)o;
        *n = b->byte_len;
        return b->data;
    }
    if (tag == PY_TYPE_BYTEARRAY) {
        PyByteArrayObject *b = (PyByteArrayObject *)o;
        *n = b->byte_len;
        return b->data;
    }
    if (tag == PY_TYPE_MEMORYVIEW) {
        PyMemoryViewObject *m = (PyMemoryViewObject *)o;
        PyObject *base = pcc_gc_load_ptr(o, &m->base);
        return asyncio_bytes_data(base, n);
    }
    if (tag == PY_TYPE_STR) {
        *n = py_str_byte_len(o);
        return py_str_utf8(o);
    }
    return NULL;
}

static PyObject *asyncio_addr_tuple(const struct sockaddr *addr, socklen_t len) {
    char host[INET6_ADDRSTRLEN];
    int64_t port = 0;
    const void *src = NULL;
    if (addr->sa_family == AF_INET && len >= (socklen_t)sizeof(struct sockaddr_in)) {
        const struct sockaddr_in *in = (const struct sockaddr_in *)addr;
        src = &in->sin_addr;
        port = (int64_t)ntohs(in->sin_port);
    } else if (
        addr->sa_family == AF_INET6
        && len >= (socklen_t)sizeof(struct sockaddr_in6)
    ) {
        const struct sockaddr_in6 *in6 = (const struct sockaddr_in6 *)addr;
        src = &in6->sin6_addr;
        port = (int64_t)ntohs(in6->sin6_port);
    } else {
        return py_tuple_new(0);
    }
    if (inet_ntop(addr->sa_family, src, host, sizeof(host)) == NULL) {
        return py_tuple_new(0);
    }
    PyObject *out = py_tuple_new(2);
    if (out == NULL) return NULL;
    py_tuple_set_item(out, 0, py_str_new(host, (int64_t)strlen(host)));
    py_tuple_set_item(out, 1, py_int_from_i64(port));
    return out;
}
#endif

PyObject *py_asyncio_tcp_listen(PyObject *host_obj, PyObject *port_obj, int64_t reuse_port) {
#ifdef _WIN32
    (void)host_obj;
    (void)port_obj;
    (void)reuse_port;
    asyncio_raise_oserror("asyncio TCP listen is not available on this platform");
    return NULL;
#else
    char port[32];
    if (asyncio_port_cstr(port_obj, port, sizeof(port)) != 0) {
        asyncio_raise_oserror("invalid TCP listen port");
        return NULL;
    }
    const char *host = asyncio_host_cstr(host_obj);
    struct addrinfo hints;
    struct addrinfo *result = NULL;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_flags = AI_PASSIVE;
    int rc = getaddrinfo(host, port, &hints, &result);
    if (rc != 0) {
        asyncio_raise_oserror("TCP listen getaddrinfo failed");
        return NULL;
    }
    int fd = -1;
    for (struct addrinfo *rp = result; rp != NULL; rp = rp->ai_next) {
        fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
        if (fd < 0) continue;
        asyncio_prepare_socket(fd);
        int one = 1;
        (void)setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
#ifdef SO_REUSEPORT
        if (reuse_port) {
            (void)setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &one, sizeof(one));
        }
#else
        (void)reuse_port;
#endif
        if (
            bind(fd, rp->ai_addr, rp->ai_addrlen) == 0 &&
            listen(fd, 128) == 0 &&
            asyncio_set_nonblocking(fd) == 0
        ) {
            break;
        }
        close(fd);
        fd = -1;
    }
    freeaddrinfo(result);
    if (fd < 0) {
        asyncio_raise_oserror("TCP listen failed");
        return NULL;
    }
    return py_int_from_i64((int64_t)fd);
#endif
}

PyObject *py_asyncio_tcp_accept(PyObject *listen_fd_obj) {
#ifdef _WIN32
    (void)listen_fd_obj;
    asyncio_raise_oserror("asyncio TCP accept is not available on this platform");
    return NULL;
#else
    int64_t listen_fd = asyncio_fd_value(listen_fd_obj);
    if (listen_fd < 0) {
        asyncio_raise_oserror("invalid TCP listen fd");
        return NULL;
    }
    int fd;
    for (;;) {
        fd = accept((int)listen_fd, NULL, NULL);
        if (fd >= 0) break;
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            py_incref(py_None);
            return py_None;
        }
        asyncio_raise_oserror("TCP accept failed");
        return NULL;
    }
    asyncio_prepare_socket(fd);
    if (asyncio_set_blocking(fd) != 0) {
        close(fd);
        asyncio_raise_oserror("TCP accept set blocking failed");
        return NULL;
    }
    return py_int_from_i64((int64_t)fd);
#endif
}

PyObject *py_asyncio_tcp_connect(PyObject *host_obj, PyObject *port_obj) {
#ifdef _WIN32
    (void)host_obj;
    (void)port_obj;
    asyncio_raise_oserror("asyncio TCP connect is not available on this platform");
    return NULL;
#else
    char port[32];
    const char *host = asyncio_host_cstr(host_obj);
    if (host == NULL) host = "127.0.0.1";
    if (asyncio_port_cstr(port_obj, port, sizeof(port)) != 0) {
        asyncio_raise_oserror("invalid TCP connect port");
        return NULL;
    }
    int fd = asyncio_socket_connect(host, port);
    if (fd < 0) {
        asyncio_raise_oserror("TCP connect failed");
        return NULL;
    }
    return py_int_from_i64((int64_t)fd);
#endif
}

PyObject *py_asyncio_fd_recv(PyObject *fd_obj, int64_t max_bytes) {
#ifdef _WIN32
    (void)fd_obj;
    (void)max_bytes;
    asyncio_raise_oserror("asyncio fd recv is not available on this platform");
    return NULL;
#else
    int64_t fd_value = asyncio_fd_value(fd_obj);
    if (fd_value < 0) {
        asyncio_raise_oserror("invalid TCP recv fd");
        return NULL;
    }
    if (max_bytes <= 0) max_bytes = 65536;
    if (max_bytes > 1048576) max_bytes = 1048576;
    char *buf = (char *)malloc((size_t)max_bytes);
    if (buf == NULL) {
        asyncio_raise_oserror("TCP recv allocation failed");
        return NULL;
    }
    ssize_t n;
    for (;;) {
        n = recv((int)fd_value, buf, (size_t)max_bytes, 0);
        if (n >= 0) break;
        if (errno == EINTR) continue;
        free(buf);
        asyncio_raise_oserror("TCP recv failed");
        return NULL;
    }
    PyObject *out = py_bytes_new(buf, (int64_t)n);
    free(buf);
    return out;
#endif
}

int64_t py_asyncio_fd_send_all(PyObject *fd_obj, PyObject *data_obj) {
#ifdef _WIN32
    (void)fd_obj;
    (void)data_obj;
    asyncio_raise_oserror("asyncio fd send is not available on this platform");
    return -1;
#else
    int64_t fd_value = asyncio_fd_value(fd_obj);
    if (fd_value < 0) {
        asyncio_raise_oserror("invalid TCP send fd");
        return -1;
    }
    int64_t n = 0;
    const char *data = asyncio_bytes_data(data_obj, &n);
    if (data == NULL && n == 0) {
        asyncio_raise_oserror("TCP send expects bytes-like data");
        return -1;
    }
    int64_t sent = 0;
    while (sent < n) {
        ssize_t rc = send((int)fd_value, data + sent, (size_t)(n - sent), 0);
        if (rc < 0) {
            if (errno == EINTR) continue;
            asyncio_raise_oserror("TCP send failed");
            return -1;
        }
        if (rc == 0) break;
        sent += (int64_t)rc;
    }
    return sent;
#endif
}

int64_t py_asyncio_fd_relay(
    PyObject *fd1_in_obj,
    PyObject *fd1_out_obj,
    PyObject *fd2_in_obj,
    PyObject *fd2_out_obj
) {
#ifdef _WIN32
    (void)fd1_in_obj;
    (void)fd1_out_obj;
    (void)fd2_in_obj;
    (void)fd2_out_obj;
    asyncio_raise_oserror("asyncio fd relay is not available on this platform");
    return -1;
#else
    int fd1_in = (int)asyncio_fd_value(fd1_in_obj);
    int fd1_out = (int)asyncio_fd_value(fd1_out_obj);
    int fd2_in = (int)asyncio_fd_value(fd2_in_obj);
    int fd2_out = (int)asyncio_fd_value(fd2_out_obj);
    if (fd1_in < 0 || fd1_out < 0 || fd2_in < 0 || fd2_out < 0) {
        asyncio_raise_oserror("invalid TCP relay fd");
        return -1;
    }
    int active1 = 1;
    int active2 = 1;
    char buf[65536];
    while (active1 || active2) {
        fd_set rfds;
        FD_ZERO(&rfds);
        int maxfd = -1;
        if (active1) {
            FD_SET(fd1_in, &rfds);
            if (fd1_in > maxfd) maxfd = fd1_in;
        }
        if (active2) {
            FD_SET(fd2_in, &rfds);
            if (fd2_in > maxfd) maxfd = fd2_in;
        }
        if (maxfd < 0) break;
        int rc = select(maxfd + 1, &rfds, NULL, NULL, NULL);
        if (rc < 0) {
            if (errno == EINTR) continue;
            asyncio_close_unique4(fd1_in, fd1_out, fd2_in, fd2_out);
            asyncio_raise_oserror("TCP relay select failed");
            return -1;
        }
        if (active1 && FD_ISSET(fd1_in, &rfds)) {
            ssize_t n = recv(fd1_in, buf, sizeof(buf), 0);
            if (n > 0) {
                if (asyncio_send_all_raw(fd1_out, buf, (size_t)n) != 0) {
                    active1 = 0;
                    shutdown(fd1_in, SHUT_RD);
                    shutdown(fd1_out, SHUT_WR);
                }
            } else if (n == 0) {
                active1 = 0;
                shutdown(fd1_out, SHUT_WR);
            } else if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK) {
                active1 = 0;
                shutdown(fd1_out, SHUT_WR);
            }
        }
        if (active2 && FD_ISSET(fd2_in, &rfds)) {
            ssize_t n = recv(fd2_in, buf, sizeof(buf), 0);
            if (n > 0) {
                if (asyncio_send_all_raw(fd2_out, buf, (size_t)n) != 0) {
                    active2 = 0;
                    shutdown(fd2_in, SHUT_RD);
                    shutdown(fd2_out, SHUT_WR);
                }
            } else if (n == 0) {
                active2 = 0;
                shutdown(fd2_out, SHUT_WR);
            } else if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK) {
                active2 = 0;
                shutdown(fd2_out, SHUT_WR);
            }
        }
    }
    asyncio_close_unique4(fd1_in, fd1_out, fd2_in, fd2_out);
    return 0;
#endif
}

/* Cooperative, non-blocking single step of a bidirectional fd relay.
 *
 * Unlike py_asyncio_fd_relay (which owns the connection for its whole lifetime
 * in one blocking select loop, and therefore stalls the single-threaded event
 * loop while ANY relayed connection stays open/idle), this performs exactly one
 * non-blocking forwarding pass and returns control to the caller so the event
 * loop can multiplex many relays plus accept new connections.
 *
 * active_mask bit0 = direction fd1_in->fd1_out still open, bit1 = fd2_in->fd2_out.
 * The mask is passed and returned as a Python int (boxed) so the cooperative
 * event-loop driver can keep per-relay state in an ordinary list without a
 * native-int->object marshal. Returns the updated mask; when it reaches 0 the
 * four fds are closed (deduped) and the relay is finished. The per-direction
 * EOF/error/shutdown semantics are kept in exact sync with py_asyncio_fd_relay
 * above. */
PyObject *py_asyncio_fd_relay_step(
    PyObject *fd1_in_obj,
    PyObject *fd1_out_obj,
    PyObject *fd2_in_obj,
    PyObject *fd2_out_obj,
    PyObject *active_mask_obj
) {
#ifdef _WIN32
    (void)fd1_in_obj;
    (void)fd1_out_obj;
    (void)fd2_in_obj;
    (void)fd2_out_obj;
    (void)active_mask_obj;
    asyncio_raise_oserror("asyncio fd relay is not available on this platform");
    return py_int_from_i64(0);
#else
    g_asyncio_relay_step_last_progress = 0;
    int fd1_in = (int)asyncio_fd_value(fd1_in_obj);
    int fd1_out = (int)asyncio_fd_value(fd1_out_obj);
    int fd2_in = (int)asyncio_fd_value(fd2_in_obj);
    int fd2_out = (int)asyncio_fd_value(fd2_out_obj);
    int64_t active_mask = py_int_value_i64(active_mask_obj);
    int active1 = (active_mask & 1) != 0;
    int active2 = (active_mask & 2) != 0;
    if (fd1_in < 0 || fd1_out < 0 || fd2_in < 0 || fd2_out < 0
        || (!active1 && !active2)) {
        asyncio_close_unique4(fd1_in, fd1_out, fd2_in, fd2_out);
        return py_None;
    }
    fd_set rfds;
    FD_ZERO(&rfds);
    int maxfd = -1;
    if (active1) {
        FD_SET(fd1_in, &rfds);
        if (fd1_in > maxfd) maxfd = fd1_in;
    }
    if (active2) {
        FD_SET(fd2_in, &rfds);
        if (fd2_in > maxfd) maxfd = fd2_in;
    }
    struct timeval tv;
    tv.tv_sec = 0;
    tv.tv_usec = 0;
    int rc = select(maxfd + 1, &rfds, NULL, NULL, &tv);
    if (rc <= 0) {
        if (rc < 0 && errno != EINTR) {
            asyncio_close_unique4(fd1_in, fd1_out, fd2_in, fd2_out);
            return py_None;
        }
        return py_int_from_i64((active1 ? 1 : 0) | (active2 ? 2 : 0));
    }
    int made_progress = 0;
    int old_active1 = active1;
    int old_active2 = active2;
    char buf[PCC_ASYNCIO_RELAY_BUF_SIZE];
    if (active1 && FD_ISSET(fd1_in, &rfds)) {
        asyncio_relay_drain_direction(
            fd1_in, fd1_out, &active1, buf, sizeof(buf), &made_progress
        );
    }
    if (active2 && FD_ISSET(fd2_in, &rfds)) {
        asyncio_relay_drain_direction(
            fd2_in, fd2_out, &active2, buf, sizeof(buf), &made_progress
        );
    }
    if ((old_active1 && !active1) || (old_active2 && !active2)) {
        active1 = 0;
        active2 = 0;
    }
    int64_t newmask = (active1 ? 1 : 0) | (active2 ? 2 : 0);
    if (newmask == 0) {
        asyncio_close_unique4(fd1_in, fd1_out, fd2_in, fd2_out);
        return py_None;
    }
    if (made_progress) {
        g_asyncio_relay_step_last_progress = 1;
        newmask |= 4;
    }
    return py_int_from_i64(newmask);
#endif
}


PyObject *py_asyncio_fd_relay_step_last_progress(void) {
    if (g_asyncio_relay_step_last_progress) {
        return py_True;
    }
    return py_None;
}

int64_t py_asyncio_fd_close(PyObject *fd_obj) {
#ifdef _WIN32
    (void)fd_obj;
    return 0;
#else
    int64_t fd_value = asyncio_fd_value(fd_obj);
    if (fd_value >= 0) {
        return close((int)fd_value);
    }
    return 0;
#endif
}

PyObject *py_asyncio_fd_sockname(PyObject *fd_obj) {
#ifdef _WIN32
    (void)fd_obj;
    return py_tuple_new(0);
#else
    int64_t fd_value = asyncio_fd_value(fd_obj);
    if (fd_value < 0) return py_tuple_new(0);
    struct sockaddr_storage addr;
    socklen_t len = (socklen_t)sizeof(addr);
    if (getsockname((int)fd_value, (struct sockaddr *)&addr, &len) != 0) {
        return py_tuple_new(0);
    }
    return asyncio_addr_tuple((const struct sockaddr *)&addr, len);
#endif
}

PyObject *py_asyncio_fd_peername(PyObject *fd_obj) {
#ifdef _WIN32
    (void)fd_obj;
    return py_tuple_new(0);
#else
    int64_t fd_value = asyncio_fd_value(fd_obj);
    if (fd_value < 0) return py_tuple_new(0);
    struct sockaddr_storage addr;
    socklen_t len = (socklen_t)sizeof(addr);
    if (getpeername((int)fd_value, (struct sockaddr *)&addr, &len) != 0) {
        return py_tuple_new(0);
    }
    return asyncio_addr_tuple((const struct sockaddr *)&addr, len);
#endif
}

/* Report which IO-waitset readiness backend this platform provides, so the
 * cooperative event loop can pick the scalable kqueue/epoll notifier when it is
 * available and otherwise fall back to the poll(2) rescan. Returns the string
 * "kqueue" on Darwin/BSD (where py_io_waitset.c has a real kevent(2) backend)
 * and "poll" everywhere else (the level-triggered fallback). This is the
 * frontend-visible seam over the new py_io_waitset.c waitset; the O(n) scheduler
 * wiring in pcc_threads.c is a later slice. */
PyObject *py_asyncio_io_waitset_backend(void) {
    if (pcc_io_waitset_kqueue_available()) {
        return py_str_new("kqueue", 6);
    }
    return py_str_new("poll", 4);
}
