#ifndef PCC_TLS_PROVIDER_V1_H
#define PCC_TLS_PROVIDER_V1_H

/*
 * Stable native TLS provider boundary for pcc.gateway.
 *
 * This header is intentionally independent of OpenSSL, BoringSSL and
 * platform TLS headers.  A provider shared library exports exactly:
 *
 *   int64_t pcc_tls_provider_v1_call(
 *       int64_t operation, pcc_tls_provider_v1_request *request);
 *
 * All sockets are already nonblocking.  The provider must never poll, wait,
 * spawn a thread, close the fd, invoke Python/libpython, or retain any input
 * or output buffer after the call.  Context and connection handles are owned
 * by the provider until their matching FREE operation.
 */

#include <stddef.h>
#include <stdint.h>

#define PCC_TLS_PROVIDER_V1_ABI 1
#define PCC_TLS_PROVIDER_V1_REQUEST_BYTES 160

enum pcc_tls_provider_v1_status {
    PCC_TLS_ERROR = -1,
    PCC_TLS_OK = 0,
    PCC_TLS_WANT_READ = 1,
    PCC_TLS_WANT_WRITE = 2,
    PCC_TLS_CLOSED = 3,
    PCC_TLS_SELECT_SNI = 4
};

enum pcc_tls_provider_v1_operation {
    PCC_TLS_OP_PROBE = 0,
    PCC_TLS_OP_CONTEXT_CREATE = 1,
    PCC_TLS_OP_CONTEXT_FREE = 2,
    PCC_TLS_OP_CONNECTION_CREATE = 3,
    PCC_TLS_OP_CONNECTION_FREE = 4,
    PCC_TLS_OP_HANDSHAKE = 5,
    PCC_TLS_OP_SET_CONTEXT = 6,
    PCC_TLS_OP_SELECTED_ALPN = 7,
    PCC_TLS_OP_READ = 8,
    PCC_TLS_OP_WRITE = 9,
    PCC_TLS_OP_CLOSE_NOTIFY = 10
};

enum pcc_tls_provider_v1_capability {
    PCC_TLS_CAP_TLS12 = UINT64_C(1) << 0,
    PCC_TLS_CAP_TLS13 = UINT64_C(1) << 1,
    PCC_TLS_CAP_CERTIFICATE_CHAIN = UINT64_C(1) << 2,
    PCC_TLS_CAP_PRIVATE_KEY = UINT64_C(1) << 3,
    PCC_TLS_CAP_SNI = UINT64_C(1) << 4,
    PCC_TLS_CAP_ALPN = UINT64_C(1) << 5,
    PCC_TLS_CAP_NONBLOCKING = UINT64_C(1) << 6,
    PCC_TLS_CAP_CLOSE_NOTIFY = UINT64_C(1) << 7,
    PCC_TLS_CAP_CLIENT_CERTIFICATE = UINT64_C(1) << 8
};

/* Stable error values mirror pcc.gateway.tls TLS_ERR_* exactly. */
enum pcc_tls_provider_v1_error {
    PCC_TLS_ERR_NONE = 0,
    PCC_TLS_ERR_DEADLINE = 1,
    PCC_TLS_ERR_CANCELLED = 2,
    PCC_TLS_ERR_PROTOCOL = 3,
    PCC_TLS_ERR_CERTIFICATE = 4,
    PCC_TLS_ERR_IO = 5,
    PCC_TLS_ERR_TRUNCATED = 6,
    PCC_TLS_ERR_UNRECOGNIZED_NAME = 7,
    PCC_TLS_ERR_ALPN = 8,
    PCC_TLS_ERR_PROVIDER = 9,
    PCC_TLS_ERR_PROVIDER_CONTRACT = 10,
    PCC_TLS_ERR_CONFIGURATION = 11,
    PCC_TLS_ERR_INTERNAL = 12
};

typedef struct pcc_tls_provider_v1_request {
    uint64_t struct_size;
    uint64_t abi_version;
    int64_t operation;
    int64_t status;
    int64_t error_code;
    void *primary;
    union {
        void *pointer;
        int64_t integer;
    } secondary;
    const void *input0;
    uint64_t input0_len;
    const void *input1;
    uint64_t input1_len;
    const void *input2;
    uint64_t input2_len;
    void *output0;
    uint64_t output0_capacity;
    uint64_t output0_len;
    uint64_t flags;
    int64_t provider_code;
    const void *input3;
    uint64_t input3_len;
} pcc_tls_provider_v1_request;

#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
_Static_assert(sizeof(void *) == 8,
               "pcc TLS provider v1 requires 64-bit pointers");
_Static_assert(
    sizeof(pcc_tls_provider_v1_request) == PCC_TLS_PROVIDER_V1_REQUEST_BYTES,
    "pcc TLS provider v1 request layout drifted");
#define PCC_TLS_V1_ASSERT_OFFSET(field, expected)                            \
    _Static_assert(offsetof(pcc_tls_provider_v1_request, field) == expected, \
                   "pcc TLS provider v1 " #field " offset drifted")
PCC_TLS_V1_ASSERT_OFFSET(struct_size, 0);
PCC_TLS_V1_ASSERT_OFFSET(abi_version, 8);
PCC_TLS_V1_ASSERT_OFFSET(operation, 16);
PCC_TLS_V1_ASSERT_OFFSET(status, 24);
PCC_TLS_V1_ASSERT_OFFSET(error_code, 32);
PCC_TLS_V1_ASSERT_OFFSET(primary, 40);
PCC_TLS_V1_ASSERT_OFFSET(secondary, 48);
PCC_TLS_V1_ASSERT_OFFSET(input0, 56);
PCC_TLS_V1_ASSERT_OFFSET(input0_len, 64);
PCC_TLS_V1_ASSERT_OFFSET(input1, 72);
PCC_TLS_V1_ASSERT_OFFSET(input1_len, 80);
PCC_TLS_V1_ASSERT_OFFSET(input2, 88);
PCC_TLS_V1_ASSERT_OFFSET(input2_len, 96);
PCC_TLS_V1_ASSERT_OFFSET(output0, 104);
PCC_TLS_V1_ASSERT_OFFSET(output0_capacity, 112);
PCC_TLS_V1_ASSERT_OFFSET(output0_len, 120);
PCC_TLS_V1_ASSERT_OFFSET(flags, 128);
PCC_TLS_V1_ASSERT_OFFSET(provider_code, 136);
PCC_TLS_V1_ASSERT_OFFSET(input3, 144);
PCC_TLS_V1_ASSERT_OFFSET(input3_len, 152);
#undef PCC_TLS_V1_ASSERT_OFFSET
#endif

#if defined(__cplusplus)
extern "C" {
#endif

int64_t pcc_tls_provider_v1_call(
    int64_t operation, pcc_tls_provider_v1_request *request);

#if defined(__cplusplus)
}
#endif

/*
 * Operation contract summary:
 *
 * PROBE:
 *   output0[capacity] <- ASCII implementation id (not NUL-required)
 *   output0_len, flags <- capability bitset
 *
 * CONTEXT_CREATE:
 *   input0 <- absolute PEM certificate-chain path
 *   input1 <- absolute unencrypted PEM private-key path
 *   input2 <- repeated decimal-length ':' ALPN tokens
 *   input3 <- absolute PEM client trust-anchor path (required when flags bit 0
 *             is set)
 *   flags bit 0 <- require client certificate
 *   primary <- new provider context
 *
 * CONNECTION_CREATE:
 *   primary <- context; secondary.integer <- nonblocking fd
 *   primary <- new provider connection on return
 *
 * HANDSHAKE:
 *   primary <- connection
 *   return WANT_READ/WANT_WRITE while incomplete.  Return SELECT_SNI once and
 *   write the normalized ASCII name to output0 before certificate flight; the
 *   next call must follow SET_CONTEXT.  Return OK only when authenticated
 *   negotiation is complete.
 *
 * SET_CONTEXT:
 *   primary <- connection; secondary.pointer <- selected context
 *
 * SELECTED_ALPN:
 *   primary <- connection; output0 <- selected ASCII protocol (or empty)
 *
 * READ / WRITE:
 *   primary <- connection; READ uses output0/capacity and writes output0_len;
 *   WRITE uses input0/input0_len and writes output0_len.  Return WANT_READ or
 *   WANT_WRITE exactly as required by the TLS state machine.
 *
 * CLOSE_NOTIFY:
 *   primary <- connection; return WANT_* until the alert is flushed/received,
 *   then CLOSED.  No operation owns an I/O deadline; pcc's virtual thread does.
 *
 * Every return value must also be written to status.  On ERROR, error_code is
 * one stable pcc_tls_provider_v1_error.  provider_code is diagnostic-only and
 * must not become an HTTP response, metric label, or public error contract.
 */

#endif
