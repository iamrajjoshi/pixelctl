/* SPDX-License-Identifier: GPL-3.0-or-later
 * Strong Linux CSPRNG entropy for Mbed TLS on musl. GRND_NONBLOCK fails closed
 * before kernel initialization; it never substitutes an unseeded RNG.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stddef.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <mbedtls/entropy.h>
int mbedtls_hardware_poll(void *context, unsigned char *output, size_t size, size_t *produced) {
    (void)context;
    *produced = 0;
    while (*produced < size) {
        ssize_t n = syscall(SYS_getrandom, output + *produced, size - *produced, 1 /* GRND_NONBLOCK */);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return MBEDTLS_ERR_ENTROPY_SOURCE_FAILED;
        *produced += (size_t)n;
    }
    return 0;
}
