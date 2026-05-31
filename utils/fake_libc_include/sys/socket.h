#include "_fake_defines.h"
#include "_fake_typedefs.h"

/* Minimal address-family / socket-type / function placeholders so pcc's
 * C parser can lower sources that use BSD socket APIs (e.g.
 * pcc/py_runtime/src/py_http.c). Values come from BSD/Darwin defaults
 * and are wide enough for both glibc and macOS at the codegen layer;
 * the actual numeric values come from the system compiler's real
 * headers at link time. */
#ifndef __PCC_FAKE_LIBC_SOCKET_DEFINED
#define __PCC_FAKE_LIBC_SOCKET_DEFINED

#define AF_UNSPEC 0
#define AF_INET 2
#define AF_INET6 30
#define SOCK_STREAM 1
#define SOCK_DGRAM 2

/* netdb.h includes sys/socket.h via the platform header chain; declare
 * ``struct sockaddr`` here as well in case sys/socket.h is included
 * without netdb.h. The matching ``addrinfo`` shape lives in netdb.h. */
#ifndef __PCC_FAKE_LIBC_SOCKADDR_DEFINED
#define __PCC_FAKE_LIBC_SOCKADDR_DEFINED
typedef unsigned int socklen_t;
struct sockaddr {
    unsigned short sa_family;
    char sa_data[14];
};
#endif

int socket(int domain, int type, int protocol);
int connect(int fd, const struct sockaddr *addr, socklen_t addrlen);
ssize_t send(int fd, const void *buf, size_t len, int flags);
ssize_t recv(int fd, void *buf, size_t len, int flags);

#endif
