/* SPDX-License-Identifier: GPL-3.0-or-later
 * Verified HTTPS utility using statically linked Mbed TLS 3.6 LTS (Apache-2.0).
 * No vendor TLS code, credentials or manufacturer services are required.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <netdb.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>
#include <mbedtls/ctr_drbg.h>
#include <mbedtls/entropy.h>
#include <mbedtls/net_sockets.h>
#include <mbedtls/ssl.h>
#include <mbedtls/x509_crt.h>
#include <psa/crypto.h>

static void quoted(const char *value, size_t size) {
    putchar('"');
    for (size_t i = 0; i < size; i++) {
        unsigned char c = value[i];
        if (c == '"' || c == '\\') { putchar('\\'); putchar(c); }
        else if (c < 32) printf("\\u%04x", c);
        else putchar(c);
    }
    putchar('"');
}
static int safe_http_value(const char *value) {
    for (const unsigned char *p = (const unsigned char *)value; *p; p++) if (*p <= 32 || *p == 127) return 0;
    return 1;
}
int main(int argc, char **argv) {
    const char *url = NULL, *ca = NULL, *token_file = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--ca") && i + 1 < argc) ca = argv[++i];
        else if (!strcmp(argv[i], "--token-file") && i + 1 < argc) token_file = argv[++i];
        else if (!url) url = argv[i];
        else { fprintf(stderr, "Unexpected argument\n"); return 1; }
    }
    if (!url || !ca || strncmp(url, "https://", 8)) {
        fprintf(stderr, "Usage: https-get https://HOST/PATH --ca CA_PEM [--token-file BEARER_FILE]\n"); return 1;
    }
    char host[256], authority[270], path[2048] = "/", port[6] = "443", token[4096] = {0};
    const char *start = url + 8, *slash = strchr(start, '/');
    size_t authority_len = slash ? (size_t)(slash - start) : strlen(start);
    if (!authority_len || authority_len >= sizeof(authority) || (slash && strlen(slash) >= sizeof(path))) return 1;
    memcpy(authority, start, authority_len); authority[authority_len] = 0;
    if (slash) strcpy(path, slash);
    char *fragment = strchr(path, '#'); if (fragment) *fragment = 0;
    if (!safe_http_value(authority) || !safe_http_value(path) || strchr(authority, '@')) { fprintf(stderr, "Unsafe URL\n"); return 1; }
    char *colon = strchr(authority, ':');
    size_t host_len = colon ? (size_t)(colon - authority) : strlen(authority);
    if (!host_len || host_len >= sizeof(host)) return 1;
    memcpy(host, authority, host_len); host[host_len] = 0;
    for (const char *p = host; *p; p++) if (!( (*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') || (*p >= '0' && *p <= '9') || *p == '.' || *p == '-')) return 1;
    if (colon) {
        char *end; long number = strtol(colon + 1, &end, 10);
        if (*end || number < 1 || number > 65535) return 1;
        snprintf(port, sizeof(port), "%ld", number);
    }
    if (token_file) {
        FILE *file = fopen(token_file, "rb");
        if (!file) { perror("token file"); return 1; }
        size_t n = fread(token, 1, sizeof(token) - 1, file);
        int extra = fgetc(file); int failed = ferror(file); fclose(file);
        if (failed || extra != EOF) { fprintf(stderr, "Token file too long or unreadable\n"); return 1; }
        while (n && (token[n - 1] == '\r' || token[n - 1] == '\n')) token[--n] = 0;
        if (!n || !safe_http_value(token)) { fprintf(stderr, "Invalid token file\n"); return 1; }
    }
    signal(SIGPIPE, SIG_IGN);
    mbedtls_ssl_context ssl; mbedtls_ssl_config config;
    mbedtls_x509_crt certificates; mbedtls_entropy_context entropy;
    mbedtls_ctr_drbg_context random;
    mbedtls_ssl_init(&ssl); mbedtls_ssl_config_init(&config);
    mbedtls_x509_crt_init(&certificates); mbedtls_entropy_init(&entropy);
    mbedtls_ctr_drbg_init(&random);
    const unsigned char personalization[] = "pixelctl-native-https";
    if (psa_crypto_init() != PSA_SUCCESS ||
        mbedtls_ctr_drbg_seed(&random, mbedtls_entropy_func, &entropy, personalization, sizeof(personalization) - 1) != 0 ||
        mbedtls_x509_crt_parse_file(&certificates, ca) < 0 ||
        mbedtls_ssl_config_defaults(&config, MBEDTLS_SSL_IS_CLIENT, MBEDTLS_SSL_TRANSPORT_STREAM, MBEDTLS_SSL_PRESET_DEFAULT) != 0) {
        fprintf(stderr, "Cannot configure verified TLS, entropy or CA bundle\n"); return 2;
    }
    mbedtls_ssl_conf_authmode(&config, MBEDTLS_SSL_VERIFY_REQUIRED);
    mbedtls_ssl_conf_ca_chain(&config, &certificates, NULL);
    mbedtls_ssl_conf_rng(&config, mbedtls_ctr_drbg_random, &random);
    mbedtls_ssl_conf_min_tls_version(&config, MBEDTLS_SSL_VERSION_TLS1_2);
    struct addrinfo hints = {.ai_socktype = SOCK_STREAM}, *addresses;
    if (getaddrinfo(host, port, &hints, &addresses)) { fprintf(stderr, "DNS lookup failed\n"); return 3; }
    int fd = -1;
    for (struct addrinfo *a = addresses; a; a = a->ai_next) {
        fd = socket(a->ai_family, a->ai_socktype, a->ai_protocol);
        if (fd < 0) continue;
        struct timeval timeout = {.tv_sec = 10};
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        if (!connect(fd, a->ai_addr, a->ai_addrlen)) break;
        close(fd); fd = -1;
    }
    freeaddrinfo(addresses);
    if (fd < 0) { fprintf(stderr, "Connection failed\n"); return 3; }
    mbedtls_net_context network = {.fd = fd};
    if (mbedtls_ssl_setup(&ssl, &config) != 0 || mbedtls_ssl_set_hostname(&ssl, host) != 0) {
        fprintf(stderr, "Cannot initialize hostname-verified TLS\n"); return 4;
    }
    mbedtls_ssl_set_bio(&ssl, &network, mbedtls_net_send, mbedtls_net_recv, NULL);
    int connected;
    time_t deadline = time(NULL) + 30;
    do { connected = mbedtls_ssl_handshake(&ssl); }
    while ((connected == MBEDTLS_ERR_SSL_WANT_READ || connected == MBEDTLS_ERR_SSL_WANT_WRITE) && time(NULL) < deadline);
    if (connected != 0 || mbedtls_ssl_get_verify_result(&ssl) != 0) {
        fprintf(stderr, "TLS certificate/hostname verification or handshake failed (code=%d, flags=%lu)\n", connected, (unsigned long)mbedtls_ssl_get_verify_result(&ssl)); return 4;
    }
    char request[8192];
    int request_size = snprintf(request, sizeof(request), "GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: pixelctl-native/0.1\r\nAccept: application/json\r\nConnection: close\r\n%s%s%s\r\n", path, authority, token_file ? "Authorization: Bearer " : "", token, token_file ? "\r\n" : "");
    if (request_size < 0 || (size_t)request_size >= sizeof(request)) return 1;
    for (int offset = 0; offset < request_size; ) {
        int n = mbedtls_ssl_write(&ssl, (const unsigned char *)request + offset, (size_t)(request_size - offset));
        if (n <= 0) { fprintf(stderr, "HTTPS request failed\n"); return 5; }
        offset += n;
    }
    const size_t limit = 1024 * 1024;
    char *response = malloc(limit + 1);
    if (!response) return 1;
    size_t total = 0;
    for (;;) {
        int n = mbedtls_ssl_read(&ssl, (unsigned char *)response + total, limit - total);
        if (n > 0) {
            total += (size_t)n;
            if (total == limit) { fprintf(stderr, "Response exceeds 1MiB limit\n"); return 6; }
        } else {
            if (n == 0 || n == MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY) break;
            if ((n == MBEDTLS_ERR_SSL_WANT_READ || n == MBEDTLS_ERR_SSL_WANT_WRITE) && time(NULL) < deadline) continue;
            fprintf(stderr, "HTTPS response incomplete\n"); return 5;
        }
    }
    response[total] = 0;
    char *body = strstr(response, "\r\n\r\n"); int status;
    if (!body || body - response > 16384 || sscanf(response, "HTTP/%*d.%*d %d", &status) != 1) { fprintf(stderr, "Invalid HTTP response\n"); return 5; }
    body += 4;
    printf("{\"status\":%d,\"tls_verified\":true,\"hostname_verified\":true,\"hostname\":", status);
    quoted(host, strlen(host)); printf(",\"tls_version\":"); quoted(mbedtls_ssl_get_version(&ssl), strlen(mbedtls_ssl_get_version(&ssl)));
    printf(",\"body\":"); quoted(body, total - (size_t)(body - response)); puts("}");
    free(response); mbedtls_ssl_free(&ssl); mbedtls_ssl_config_free(&config); mbedtls_x509_crt_free(&certificates); mbedtls_ctr_drbg_free(&random); mbedtls_entropy_free(&entropy); close(fd);
    return status >= 200 && status < 300 ? 0 : 22;
}
