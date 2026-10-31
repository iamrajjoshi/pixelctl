/* SPDX-License-Identifier: GPL-3.0-or-later
 * Guarded NOR updater for the TC002 res partition only. This never touches the
 * kernel, rootfs, configuration, MCU or recovery partitions.
 * Both input files are erase-block-aligned prefixes. The remaining partition
 * must be erased (0xff). A previously interrupted write can resume only when
 * every block matches either the known original or the intended new bytes.
 */
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>
#define BLOCK 65536U
#define PARTITION (8U * 1024U * 1024U)
struct mtd_info_user { uint8_t type; uint32_t flags, size, erasesize, writesize, oobsize; uint64_t padding; };
struct erase_info_user { uint32_t start, length; };
#define MEMGETINFO _IOR('M', 1, struct mtd_info_user)
#define MEMERASE _IOW('M', 2, struct erase_info_user)
static unsigned char actual[BLOCK], before[BLOCK], after[BLOCK];
static int read_exact(int fd, void *data, size_t size, off_t offset) {
    unsigned char *p = data;
    while (size) {
        ssize_t n = pread(fd, p, size, offset);
        if (n <= 0) return -1;
        p += n; size -= (size_t)n; offset += n;
    }
    return 0;
}
static int write_exact(int fd, const void *data, size_t size, off_t offset) {
    const unsigned char *p = data;
    while (size) {
        ssize_t n = pwrite(fd, p, size, offset);
        if (n <= 0) return -1;
        p += n; size -= (size_t)n; offset += n;
    }
    return 0;
}
int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "Usage: res-flash OLD_PREFIX NEW_PREFIX [--write] [--simulate-file FILE]\n"); return 1; }
    int apply = 0, simulate = 0, recover = 0;
    const char *device = "/dev/mtd/mtd3";
    for (int i = 3; i < argc; i++) {
        if (!strcmp(argv[i], "--write")) apply = 1;
        else if (!strcmp(argv[i], "--recover-partial")) recover = 1;
        else if (!strcmp(argv[i], "--simulate-file") && i + 1 < argc) { simulate = 1; device = argv[++i]; }
        else { fprintf(stderr, "Unknown argument\n"); return 1; }
    }
    int oldfd = open(argv[1], O_RDONLY), newfd = open(argv[2], O_RDONLY);
    int fd = open(device, apply ? O_RDWR | O_SYNC : O_RDONLY);
    if (oldfd < 0 || newfd < 0 || fd < 0) { perror("open input/device"); return 1; }
    struct stat oldstat, newstat, devstat;
    if (fstat(oldfd, &oldstat) || fstat(newfd, &newstat) || fstat(fd, &devstat)) return 1;
    if (oldstat.st_size != newstat.st_size || oldstat.st_size < BLOCK || oldstat.st_size > PARTITION || oldstat.st_size % BLOCK) {
        fprintf(stderr, "Refused: input sizes must match, be 64KiB aligned and fit res\n"); return 1;
    }
    if (simulate) {
        if (!S_ISREG(devstat.st_mode) || devstat.st_size != PARTITION) { fprintf(stderr, "Invalid simulation file\n"); return 1; }
    } else {
        FILE *partitions = fopen("/proc/mtd", "r");
        char line[160], name[80]; unsigned int size, erase; int matched = 0;
        if (!partitions) return 1;
        while (fgets(line, sizeof(line), partitions)) {
            if (sscanf(line, "mtd3: %x %x \"%79[^\"]\"", &size, &erase, name) == 3 && !strcmp(name, "res") && size == PARTITION && erase == BLOCK) matched = 1;
        }
        fclose(partitions);
        if (!matched) { fprintf(stderr, "Refused: mtd3 is not the expected res partition\n"); return 1; }
        struct mtd_info_user info;
        if (ioctl(fd, MEMGETINFO, &info) < 0) { perror("MEMGETINFO"); return 1; }
        if (info.size != PARTITION || info.erasesize != BLOCK || info.type != 3) {
            fprintf(stderr, "Refused: expected 8MiB NOR res partition with 64KiB erase blocks\n"); return 1;
        }
    }
    unsigned int changed = 0;
    /* Complete read-only preflight before the first erase. */
    for (off_t offset = 0; offset < PARTITION; offset += BLOCK) {
        if (read_exact(fd, actual, BLOCK, offset) < 0) { perror("read partition"); return 1; }
        if (offset < oldstat.st_size) {
            if (read_exact(oldfd, before, BLOCK, offset) < 0 || read_exact(newfd, after, BLOCK, offset) < 0) return 1;
            if (!recover && memcmp(actual, before, BLOCK) && memcmp(actual, after, BLOCK)) {
                fprintf(stderr, "Refused: unknown live bytes in block %lld\n", (long long)(offset / BLOCK)); return 1;
            }
            if (memcmp(actual, after, BLOCK)) changed++;
        } else {
            for (size_t i = 0; i < BLOCK; i++) if (actual[i] != 255) {
                fprintf(stderr, "Refused: non-erased bytes beyond the supplied prefix\n"); return 1;
            }
        }
    }
    printf("{\"preflight\":true,\"blocks_to_write\":%u,\"dry_run\":%s}\n", changed, apply ? "false" : "true"); fflush(stdout);
    if (!apply) return 0;
    for (off_t offset = 0; offset < newstat.st_size; offset += BLOCK) {
        if (read_exact(fd, actual, BLOCK, offset) < 0 || read_exact(newfd, after, BLOCK, offset) < 0) return 1;
        if (!memcmp(actual, after, BLOCK)) continue;
        if (!simulate) {
            struct erase_info_user erase = {.start = (uint32_t)offset, .length = BLOCK};
            if (ioctl(fd, MEMERASE, &erase) < 0) { perror("erase res block; keep power on and retry"); return 1; }
        }
        if (write_exact(fd, after, BLOCK, offset) < 0) { perror("write res block; keep power on and retry"); return 1; }
        if (fsync(fd) < 0 && errno != EINVAL && errno != ENOTSUP) { perror("sync res block; keep power on and retry"); return 1; }
        if (read_exact(fd, actual, BLOCK, offset) < 0 || memcmp(actual, after, BLOCK)) {
            fprintf(stderr, "Verification failed; keep power on and retry\n"); return 1;
        }
    }
    for (off_t offset = 0; offset < newstat.st_size; offset += BLOCK) {
        if (read_exact(fd, actual, BLOCK, offset) < 0 || read_exact(newfd, after, BLOCK, offset) < 0 || memcmp(actual, after, BLOCK)) return 1;
    }
    printf("{\"verified\":true,\"blocks_written\":%u}\n", changed);
    close(fd); close(oldfd); close(newfd);
    return 0;
}
