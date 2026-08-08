/*
 * pcc-native-tls-v1 provider backed by OpenSSL.
 *
 * Copyright (c) pcc contributors.  SPDX-License-Identifier: MIT
 *
 * This adapter is original pcc code and links to OpenSSL through its public
 * API.  OpenSSL 3.x is Apache-2.0 licensed; downstream binary distributors
 * remain responsible for carrying the applicable OpenSSL notices.  The
 * minimum supported build and runtime version is OpenSSL 3.0.0.  BoringSSL is
 * deliberately not accepted by this implementation: it is API-related but
 * does not promise OpenSSL ABI compatibility and needs a separate provider.
 *
 * The provider owns only SSL_CTX/SSL objects, TLS records, authentication and
 * cryptography.  It never waits, polls, spawns a thread, closes the socket,
 * dispatches HTTP, or calls Python/libpython.  All supplied sockets must
 * already be nonblocking; WANT_READ/WANT_WRITE is returned to pcc's virtual-
 * thread scheduler.
 *
 * Certificate inputs are absolute or caller-resolved filesystem paths:
 * input0 = PEM certificate chain, input1 = PEM private key, input3 = optional
 * PEM client trust anchors.  Password-protected keys are intentionally
 * rejected because the v1 ABI has no secret callback surface.
 */

#define OPENSSL_API_COMPAT 30000

#include "../include/pcc_tls_provider_v1.h"

#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include <openssl/crypto.h>
#include <openssl/err.h>
#include <openssl/ssl.h>
#include <openssl/sslerr.h>
#include <openssl/tls1.h>
#include <openssl/x509_vfy.h>

#if defined(OPENSSL_IS_BORINGSSL) || defined(BORINGSSL_API_VERSION)
#error "BoringSSL requires a separately reviewed pcc TLS provider"
#elif defined(LIBRESSL_VERSION_NUMBER)
#error "LibreSSL requires a separately reviewed pcc TLS provider"
#elif !defined(OPENSSL_VERSION_MAJOR) || OPENSSL_VERSION_MAJOR < 3
#error "pcc OpenSSL TLS provider requires OpenSSL >= 3.0.0"
#endif

#define PCC_TLS_PATH_MAX UINT64_C(4096)
#define PCC_TLS_ALPN_WIRE_MAX UINT64_C(8192)
#define PCC_TLS_SNI_MAX UINT64_C(253)

#if defined(_WIN32)
#define PCC_TLS_EXPORT __declspec(dllexport)
#else
#define PCC_TLS_EXPORT __attribute__((visibility("default")))
#endif

typedef struct pcc_openssl_context {
    SSL_CTX *ssl_ctx;
    unsigned char *alpn;
    unsigned int alpn_len;
} pcc_openssl_context;

typedef struct pcc_openssl_connection {
    SSL *ssl;
    int client_hello_pending;
    int client_hello_resumed;
    int handshake_complete;
} pcc_openssl_connection;

static int pcc_tls_reject_private_key_password(char *buffer, int size,
                                               int writing, void *argument)
{
    (void)buffer;
    (void)size;
    (void)writing;
    (void)argument;
    /* ABI v1 has no secret callback, so never fall back to a terminal read. */
    return 0;
}

static void pcc_tls_clear_request(pcc_tls_provider_v1_request *request,
                                  int64_t operation)
{
    request->operation = operation;
    request->status = PCC_TLS_ERROR;
    request->error_code = PCC_TLS_ERR_NONE;
    request->output0_len = 0;
    request->provider_code = 0;
}

static int64_t pcc_tls_finish(pcc_tls_provider_v1_request *request,
                              int64_t status, int64_t error_code)
{
    request->status = status;
    request->error_code = error_code;
    return status;
}

static int64_t pcc_tls_error(pcc_tls_provider_v1_request *request,
                             int64_t error_code)
{
    unsigned long provider_code = ERR_peek_last_error();
    request->provider_code = (int64_t)provider_code;
    ERR_clear_error();
    return pcc_tls_finish(request, PCC_TLS_ERROR, error_code);
}

static int pcc_tls_request_valid(const pcc_tls_provider_v1_request *request,
                                 int64_t operation)
{
    return request != NULL
        && request->struct_size == PCC_TLS_PROVIDER_V1_REQUEST_BYTES
        && request->abi_version == PCC_TLS_PROVIDER_V1_ABI
        && request->operation == operation;
}

static char *pcc_tls_copy_path(const void *input, uint64_t length)
{
    char *path;
    if (input == NULL || length == 0 || length > PCC_TLS_PATH_MAX)
        return NULL;
    if (memchr(input, '\0', (size_t)length) != NULL)
        return NULL;
    path = malloc((size_t)length + 1);
    if (path == NULL)
        return NULL;
    memcpy(path, input, (size_t)length);
    path[length] = '\0';
    return path;
}

static int pcc_tls_parse_decimal(const unsigned char *input, size_t length,
                                 size_t *position, size_t *value)
{
    size_t current = 0;
    size_t digits = 0;
    while (*position < length && input[*position] >= '0'
           && input[*position] <= '9') {
        unsigned int digit = input[*position] - '0';
        if (current > (SIZE_MAX - digit) / 10)
            return 0;
        current = current * 10 + digit;
        ++*position;
        ++digits;
    }
    if (digits == 0 || *position >= length || input[*position] != ':')
        return 0;
    ++*position;
    if (current == 0 || current > 255 || current > length - *position)
        return 0;
    *value = current;
    return 1;
}

static int pcc_tls_parse_alpn(const void *input, uint64_t input_length,
                              unsigned char **output,
                              unsigned int *output_length)
{
    const unsigned char *source = input;
    unsigned char *wire;
    size_t input_position = 0;
    size_t output_position = 0;
    if (source == NULL || input_length == 0
        || input_length > PCC_TLS_ALPN_WIRE_MAX)
        return 0;
    wire = malloc((size_t)input_length + 1);
    if (wire == NULL)
        return 0;
    while (input_position < (size_t)input_length) {
        size_t protocol_length = 0;
        if (!pcc_tls_parse_decimal(source, (size_t)input_length,
                                   &input_position, &protocol_length)) {
            free(wire);
            return 0;
        }
        if (output_position + 1 + protocol_length > (size_t)input_length + 1) {
            free(wire);
            return 0;
        }
        wire[output_position++] = (unsigned char)protocol_length;
        memcpy(wire + output_position, source + input_position,
               protocol_length);
        output_position += protocol_length;
        input_position += protocol_length;
    }
    if (output_position == 0 || output_position > UINT_MAX) {
        free(wire);
        return 0;
    }
    *output = wire;
    *output_length = (unsigned int)output_position;
    return 1;
}

static int pcc_tls_alpn_select(SSL *ssl, const unsigned char **output,
                               unsigned char *output_length,
                               const unsigned char *client,
                               unsigned int client_length, void *argument)
{
    pcc_openssl_context *context = argument;
    unsigned char *selected = NULL;
    unsigned char selected_length = 0;
    int result;
    (void)ssl;
    if (context == NULL || context->alpn == NULL || context->alpn_len == 0)
        return SSL_TLSEXT_ERR_ALERT_FATAL;
    result = SSL_select_next_proto(&selected, &selected_length,
                                   context->alpn, context->alpn_len,
                                   client, client_length);
    if (result != OPENSSL_NPN_NEGOTIATED || selected == NULL
        || selected_length == 0)
        return SSL_TLSEXT_ERR_ALERT_FATAL;
    *output = selected;
    *output_length = selected_length;
    return SSL_TLSEXT_ERR_OK;
}

static int pcc_tls_client_hello(SSL *ssl, int *alert, void *argument)
{
    pcc_openssl_connection *connection = SSL_get_app_data(ssl);
    const unsigned char *server_name = NULL;
    size_t server_name_length = 0;
    (void)argument;
    if (connection == NULL) {
        *alert = SSL_AD_INTERNAL_ERROR;
        return SSL_CLIENT_HELLO_ERROR;
    }
    if (connection->client_hello_resumed)
        return SSL_CLIENT_HELLO_SUCCESS;
    if (!SSL_client_hello_get0_ext(ssl, TLSEXT_TYPE_server_name,
                                   &server_name, &server_name_length)) {
        connection->client_hello_resumed = 1;
        return SSL_CLIENT_HELLO_SUCCESS;
    }
    connection->client_hello_pending = 1;
    return SSL_CLIENT_HELLO_RETRY;
}

static int64_t pcc_tls_map_known_ssl_error(
    pcc_tls_provider_v1_request *request, int ssl_error, int result,
    int protocol_error)
{
    unsigned long provider_error = ERR_peek_last_error();
    request->provider_code = ssl_error;
    if (ssl_error == SSL_ERROR_WANT_READ)
        return pcc_tls_finish(request, PCC_TLS_WANT_READ, PCC_TLS_ERR_NONE);
    if (ssl_error == SSL_ERROR_WANT_WRITE)
        return pcc_tls_finish(request, PCC_TLS_WANT_WRITE, PCC_TLS_ERR_NONE);
    if (ssl_error == SSL_ERROR_ZERO_RETURN)
        return pcc_tls_finish(request, PCC_TLS_CLOSED, PCC_TLS_ERR_NONE);
    if (ssl_error == SSL_ERROR_SSL
        && ERR_GET_LIB(provider_error) == ERR_LIB_SSL
        && ERR_GET_REASON(provider_error)
               == SSL_R_UNEXPECTED_EOF_WHILE_READING)
        return pcc_tls_error(request, PCC_TLS_ERR_TRUNCATED);
    if (ssl_error == SSL_ERROR_SYSCALL && result == 0
        && provider_error == 0)
        return pcc_tls_error(request, PCC_TLS_ERR_TRUNCATED);
    if (ssl_error == SSL_ERROR_SYSCALL && errno != 0) {
        request->provider_code = errno;
        ERR_clear_error();
        return pcc_tls_finish(request, PCC_TLS_ERROR, PCC_TLS_ERR_IO);
    }
    return pcc_tls_error(request, protocol_error);
}

static int64_t pcc_tls_map_ssl_error(pcc_tls_provider_v1_request *request,
                                     SSL *ssl, int result, int protocol_error)
{
    int ssl_error = SSL_get_error(ssl, result);
    return pcc_tls_map_known_ssl_error(request, ssl_error, result,
                                       protocol_error);
}

static int64_t pcc_tls_probe(pcc_tls_provider_v1_request *request)
{
    static const char identity[] = "pcc-openssl-3-provider-v1";
    unsigned long runtime_version = OpenSSL_version_num();
    if (runtime_version < UINT64_C(0x30000000))
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER);
    if (request->output0 == NULL
        || request->output0_capacity < sizeof(identity) - 1)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    memcpy(request->output0, identity, sizeof(identity) - 1);
    request->output0_len = sizeof(identity) - 1;
    request->flags = PCC_TLS_CAP_TLS12
        | PCC_TLS_CAP_TLS13
        | PCC_TLS_CAP_CERTIFICATE_CHAIN
        | PCC_TLS_CAP_PRIVATE_KEY
        | PCC_TLS_CAP_SNI
        | PCC_TLS_CAP_ALPN
        | PCC_TLS_CAP_NONBLOCKING
        | PCC_TLS_CAP_CLOSE_NOTIFY
        | PCC_TLS_CAP_CLIENT_CERTIFICATE;
    return pcc_tls_finish(request, PCC_TLS_OK, PCC_TLS_ERR_NONE);
}

static int64_t pcc_tls_context_create(pcc_tls_provider_v1_request *request)
{
    pcc_openssl_context *context = NULL;
    char *certificate_path = NULL;
    char *private_key_path = NULL;
    char *client_ca_path = NULL;
    STACK_OF(X509_NAME) *client_ca_names = NULL;
    int require_client_certificate = (request->flags & UINT64_C(1)) != 0;

    if ((request->flags & ~UINT64_C(1)) != 0)
        goto configuration_error;

    certificate_path = pcc_tls_copy_path(request->input0,
                                         request->input0_len);
    private_key_path = pcc_tls_copy_path(request->input1,
                                         request->input1_len);
    if (certificate_path == NULL || private_key_path == NULL)
        goto certificate_error;
    if (certificate_path[0] != '/' || private_key_path[0] != '/')
        goto configuration_error;
    if (require_client_certificate) {
        client_ca_path = pcc_tls_copy_path(request->input3,
                                           request->input3_len);
        if (client_ca_path == NULL)
            goto certificate_error;
        if (client_ca_path[0] != '/')
            goto configuration_error;
    } else if (request->input3_len != 0) {
        goto certificate_error;
    }

    context = calloc(1, sizeof(*context));
    if (context == NULL)
        goto provider_error;
    if (!pcc_tls_parse_alpn(request->input2, request->input2_len,
                            &context->alpn, &context->alpn_len))
        goto configuration_error;
    context->ssl_ctx = SSL_CTX_new(TLS_server_method());
    if (context->ssl_ctx == NULL)
        goto provider_error;
    SSL_CTX_set_default_passwd_cb(context->ssl_ctx,
                                  pcc_tls_reject_private_key_password);
    if (!SSL_CTX_set_min_proto_version(context->ssl_ctx, TLS1_2_VERSION)
        || !SSL_CTX_set_max_proto_version(context->ssl_ctx, TLS1_3_VERSION))
        goto provider_error;
    SSL_CTX_set_options(context->ssl_ctx,
                        SSL_OP_NO_COMPRESSION | SSL_OP_NO_RENEGOTIATION);
    SSL_CTX_set_security_level(context->ssl_ctx, 2);
    SSL_CTX_set_mode(context->ssl_ctx,
                     SSL_MODE_ENABLE_PARTIAL_WRITE
                     | SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER
                     | SSL_MODE_RELEASE_BUFFERS);
    if (SSL_CTX_use_certificate_chain_file(context->ssl_ctx,
                                           certificate_path) != 1
        || SSL_CTX_use_PrivateKey_file(context->ssl_ctx, private_key_path,
                                       SSL_FILETYPE_PEM) != 1
        || SSL_CTX_check_private_key(context->ssl_ctx) != 1)
        goto certificate_error;
    if (require_client_certificate) {
        if (SSL_CTX_load_verify_locations(context->ssl_ctx, client_ca_path,
                                          NULL) != 1)
            goto certificate_error;
        client_ca_names = SSL_load_client_CA_file(client_ca_path);
        if (client_ca_names == NULL)
            goto certificate_error;
        SSL_CTX_set_client_CA_list(context->ssl_ctx, client_ca_names);
        client_ca_names = NULL;
        SSL_CTX_set_verify(context->ssl_ctx,
                           SSL_VERIFY_PEER | SSL_VERIFY_FAIL_IF_NO_PEER_CERT,
                           NULL);
        SSL_CTX_set_verify_depth(context->ssl_ctx, 9);
    } else {
        SSL_CTX_set_verify(context->ssl_ctx, SSL_VERIFY_NONE, NULL);
    }
    SSL_CTX_set_alpn_select_cb(context->ssl_ctx, pcc_tls_alpn_select, context);
    SSL_CTX_set_client_hello_cb(context->ssl_ctx, pcc_tls_client_hello, NULL);

    request->primary = context;
    free(certificate_path);
    free(private_key_path);
    free(client_ca_path);
    return pcc_tls_finish(request, PCC_TLS_OK, PCC_TLS_ERR_NONE);

configuration_error:
    if (client_ca_names != NULL)
        sk_X509_NAME_pop_free(client_ca_names, X509_NAME_free);
    if (context != NULL) {
        SSL_CTX_free(context->ssl_ctx);
        free(context->alpn);
        free(context);
    }
    free(certificate_path);
    free(private_key_path);
    free(client_ca_path);
    return pcc_tls_error(request, PCC_TLS_ERR_CONFIGURATION);

certificate_error:
    if (client_ca_names != NULL)
        sk_X509_NAME_pop_free(client_ca_names, X509_NAME_free);
    if (context != NULL) {
        SSL_CTX_free(context->ssl_ctx);
        free(context->alpn);
        free(context);
    }
    free(certificate_path);
    free(private_key_path);
    free(client_ca_path);
    return pcc_tls_error(request, PCC_TLS_ERR_CERTIFICATE);

provider_error:
    if (client_ca_names != NULL)
        sk_X509_NAME_pop_free(client_ca_names, X509_NAME_free);
    if (context != NULL) {
        SSL_CTX_free(context->ssl_ctx);
        free(context->alpn);
        free(context);
    }
    free(certificate_path);
    free(private_key_path);
    free(client_ca_path);
    return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER);
}

static int64_t pcc_tls_context_free(pcc_tls_provider_v1_request *request)
{
    pcc_openssl_context *context = request->primary;
    if (context == NULL)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    SSL_CTX_free(context->ssl_ctx);
    free(context->alpn);
    free(context);
    request->primary = NULL;
    return pcc_tls_finish(request, PCC_TLS_CLOSED, PCC_TLS_ERR_NONE);
}

static int64_t pcc_tls_connection_create(pcc_tls_provider_v1_request *request)
{
    pcc_openssl_context *context = request->primary;
    pcc_openssl_connection *connection;
    int fd;
    if (context == NULL || context->ssl_ctx == NULL
        || request->secondary.integer < 0
        || request->secondary.integer > INT_MAX)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    fd = (int)request->secondary.integer;
    connection = calloc(1, sizeof(*connection));
    if (connection == NULL)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER);
    connection->ssl = SSL_new(context->ssl_ctx);
    if (connection->ssl == NULL) {
        free(connection);
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER);
    }
    SSL_set_app_data(connection->ssl, connection);
    SSL_set_accept_state(connection->ssl);
    SSL_set_options(connection->ssl, SSL_OP_NO_RENEGOTIATION);
    if (SSL_set_fd(connection->ssl, fd) != 1) {
        SSL_free(connection->ssl);
        free(connection);
        return pcc_tls_error(request, PCC_TLS_ERR_IO);
    }
    request->primary = connection;
    return pcc_tls_finish(request, PCC_TLS_OK, PCC_TLS_ERR_NONE);
}

static int64_t pcc_tls_connection_free(pcc_tls_provider_v1_request *request)
{
    pcc_openssl_connection *connection = request->primary;
    if (connection == NULL)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    SSL_free(connection->ssl);
    free(connection);
    request->primary = NULL;
    return pcc_tls_finish(request, PCC_TLS_CLOSED, PCC_TLS_ERR_NONE);
}

static int pcc_tls_copy_client_sni(pcc_tls_provider_v1_request *request,
                                   SSL *ssl)
{
    const unsigned char *extension = NULL;
    size_t extension_length = 0;
    size_t name_length;
    const unsigned char *name;
    if (!SSL_client_hello_get0_ext(ssl, TLSEXT_TYPE_server_name,
                                   &extension, &extension_length)) {
        request->output0_len = 0;
        return 1;
    }
    if (extension == NULL || extension_length < 5
        || (((size_t)extension[0] << 8) | extension[1]) + 2
               != extension_length
        || extension[2] != TLSEXT_NAMETYPE_host_name)
        return 0;
    name_length = ((size_t)extension[3] << 8) | extension[4];
    if (name_length == 0 || name_length > PCC_TLS_SNI_MAX
        || name_length + 5 != extension_length)
        return 0;
    name = extension + 5;
    if (memchr(name, '\0', name_length) != NULL
        || request->output0 == NULL
        || request->output0_capacity < name_length)
        return 0;
    memcpy(request->output0, name, name_length);
    request->output0_len = name_length;
    return 1;
}

static int64_t pcc_tls_handshake(pcc_tls_provider_v1_request *request)
{
    pcc_openssl_connection *connection = request->primary;
    int result;
    int ssl_error;
    if (connection == NULL || connection->ssl == NULL)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    ERR_clear_error();
    errno = 0;
    result = SSL_do_handshake(connection->ssl);
    if (result == 1) {
        connection->handshake_complete = 1;
        return pcc_tls_finish(request, PCC_TLS_OK, PCC_TLS_ERR_NONE);
    }
    ssl_error = SSL_get_error(connection->ssl, result);
    if (ssl_error == SSL_ERROR_WANT_CLIENT_HELLO_CB
        && connection->client_hello_pending
        && !connection->client_hello_resumed) {
        if (!pcc_tls_copy_client_sni(request, connection->ssl))
            return pcc_tls_error(request, PCC_TLS_ERR_PROTOCOL);
        return pcc_tls_finish(request, PCC_TLS_SELECT_SNI, PCC_TLS_ERR_NONE);
    }
    return pcc_tls_map_known_ssl_error(request, ssl_error, result,
                                       PCC_TLS_ERR_PROTOCOL);
}

static int64_t pcc_tls_set_context(pcc_tls_provider_v1_request *request)
{
    pcc_openssl_connection *connection = request->primary;
    pcc_openssl_context *context = request->secondary.pointer;
    if (connection == NULL || connection->ssl == NULL || context == NULL
        || context->ssl_ctx == NULL || !connection->client_hello_pending
        || connection->client_hello_resumed)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    if (SSL_set_SSL_CTX(connection->ssl, context->ssl_ctx) == NULL)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER);
    /* SSL_set_SSL_CTX historically updates certificates but not every policy
     * field.  Mirror the selected generation's verification and options even
     * though pcc currently builds all SNI contexts from one TlsConfig. */
    SSL_set_verify(connection->ssl,
                   SSL_CTX_get_verify_mode(context->ssl_ctx),
                   SSL_CTX_get_verify_callback(context->ssl_ctx));
    SSL_set_verify_depth(connection->ssl,
                         SSL_CTX_get_verify_depth(context->ssl_ctx));
    SSL_clear_options(
        connection->ssl,
        SSL_get_options(connection->ssl)
            & ~SSL_CTX_get_options(context->ssl_ctx));
    SSL_set_options(connection->ssl, SSL_CTX_get_options(context->ssl_ctx));
    SSL_set_security_level(
        connection->ssl, SSL_CTX_get_security_level(context->ssl_ctx));
    connection->client_hello_resumed = 1;
    connection->client_hello_pending = 0;
    return pcc_tls_finish(request, PCC_TLS_OK, PCC_TLS_ERR_NONE);
}

static int64_t pcc_tls_selected_alpn(pcc_tls_provider_v1_request *request)
{
    pcc_openssl_connection *connection = request->primary;
    const unsigned char *selected = NULL;
    unsigned int selected_length = 0;
    if (connection == NULL || connection->ssl == NULL
        || !connection->handshake_complete)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    SSL_get0_alpn_selected(connection->ssl, &selected, &selected_length);
    if (selected_length > request->output0_capacity
        || (selected_length != 0 && request->output0 == NULL))
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    if (selected_length != 0)
        memcpy(request->output0, selected, selected_length);
    request->output0_len = selected_length;
    return pcc_tls_finish(request, PCC_TLS_OK, PCC_TLS_ERR_NONE);
}

static int64_t pcc_tls_read(pcc_tls_provider_v1_request *request)
{
    pcc_openssl_connection *connection = request->primary;
    size_t count = 0;
    int result;
    if (connection == NULL || connection->ssl == NULL
        || !connection->handshake_complete || request->output0 == NULL
        || request->output0_capacity == 0)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    ERR_clear_error();
    errno = 0;
    result = SSL_read_ex(connection->ssl, request->output0,
                         (size_t)request->output0_capacity, &count);
    if (result == 1) {
        if (count == 0)
            return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
        request->output0_len = count;
        return pcc_tls_finish(request, PCC_TLS_OK, PCC_TLS_ERR_NONE);
    }
    return pcc_tls_map_ssl_error(request, connection->ssl, result,
                                 PCC_TLS_ERR_PROTOCOL);
}

static int64_t pcc_tls_write(pcc_tls_provider_v1_request *request)
{
    pcc_openssl_connection *connection = request->primary;
    size_t count = 0;
    int result;
    if (connection == NULL || connection->ssl == NULL
        || !connection->handshake_complete || request->input0 == NULL
        || request->input0_len == 0)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    ERR_clear_error();
    errno = 0;
    result = SSL_write_ex(connection->ssl, request->input0,
                          (size_t)request->input0_len, &count);
    if (result == 1) {
        if (count == 0)
            return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
        request->output0_len = count;
        return pcc_tls_finish(request, PCC_TLS_OK, PCC_TLS_ERR_NONE);
    }
    return pcc_tls_map_ssl_error(request, connection->ssl, result,
                                 PCC_TLS_ERR_PROTOCOL);
}

static int64_t pcc_tls_close_notify(pcc_tls_provider_v1_request *request)
{
    pcc_openssl_connection *connection = request->primary;
    int result;
    if (connection == NULL || connection->ssl == NULL
        || !connection->handshake_complete)
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    ERR_clear_error();
    errno = 0;
    result = SSL_shutdown(connection->ssl);
    if (result == 1)
        return pcc_tls_finish(request, PCC_TLS_CLOSED, PCC_TLS_ERR_NONE);
    if (result == 0)
        return pcc_tls_finish(request, PCC_TLS_WANT_READ, PCC_TLS_ERR_NONE);
    return pcc_tls_map_ssl_error(request, connection->ssl, result,
                                 PCC_TLS_ERR_PROTOCOL);
}

PCC_TLS_EXPORT int64_t pcc_tls_provider_v1_call(
    int64_t operation, pcc_tls_provider_v1_request *request)
{
    if (!pcc_tls_request_valid(request, operation)) {
        if (request != NULL
            && request->struct_size >= 5 * sizeof(uint64_t)) {
            request->status = PCC_TLS_ERROR;
            request->error_code = PCC_TLS_ERR_PROVIDER_CONTRACT;
        }
        return PCC_TLS_ERROR;
    }
    pcc_tls_clear_request(request, operation);
    ERR_clear_error();
    switch (operation) {
    case PCC_TLS_OP_PROBE:
        return pcc_tls_probe(request);
    case PCC_TLS_OP_CONTEXT_CREATE:
        return pcc_tls_context_create(request);
    case PCC_TLS_OP_CONTEXT_FREE:
        return pcc_tls_context_free(request);
    case PCC_TLS_OP_CONNECTION_CREATE:
        return pcc_tls_connection_create(request);
    case PCC_TLS_OP_CONNECTION_FREE:
        return pcc_tls_connection_free(request);
    case PCC_TLS_OP_HANDSHAKE:
        return pcc_tls_handshake(request);
    case PCC_TLS_OP_SET_CONTEXT:
        return pcc_tls_set_context(request);
    case PCC_TLS_OP_SELECTED_ALPN:
        return pcc_tls_selected_alpn(request);
    case PCC_TLS_OP_READ:
        return pcc_tls_read(request);
    case PCC_TLS_OP_WRITE:
        return pcc_tls_write(request);
    case PCC_TLS_OP_CLOSE_NOTIFY:
        return pcc_tls_close_notify(request);
    default:
        return pcc_tls_error(request, PCC_TLS_ERR_PROVIDER_CONTRACT);
    }
}
