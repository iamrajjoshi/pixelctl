/* SPDX-License-Identifier: GPL-3.0-or-later
 * Let the vendor initialize Wi-Fi/MCU, then hand the display to pixelctl.
 * A failed or terminated native receiver resumes the original GUI.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/ioctl.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
static int seed_kernel(void) {
#ifdef __linux__
    const char *path = "/data/pixelctl/entropy.seed";
    const char *used = "/data/pixelctl/entropy.seed.used";
    const char *next = "/data/pixelctl/entropy.seed.new";
    const char *ready = "/tmp/pixelctl-entropy.ready";
    const char *lock_path = "/tmp/pixelctl-entropy.lock";
    if (access(ready, F_OK) == 0) return 0;
    int lock = open(lock_path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (lock < 0) return -1;
    int fd = -1, directory = -1, result = -1, consumed = 0, credited = 0;
    struct { int bits, size; unsigned char bytes[32]; } seed = {.bits = 256, .size = 32};
    /* Consume before crediting: an interrupted rotation cannot reuse a seed. */
    if (rename(path, used) < 0) goto done;
    consumed = 1;
    directory = open("/data/pixelctl", O_RDONLY | O_DIRECTORY);
    if (directory < 0 || fsync(directory) < 0) goto done;
    fd = open(used, O_RDONLY);
    if (fd < 0 || read(fd, seed.bytes, sizeof(seed.bytes)) != sizeof(seed.bytes)) goto done;
    close(fd); fd = open("/dev/urandom", O_RDWR);
    if (fd < 0 || ioctl(fd, _IOW('R', 0x03, int[2]) /* RNDADDENTROPY */, &seed) < 0) goto done;
    credited = 1;
    /* Credit once per boot, even if the supervisor later restarts. */
    int marker = open(ready, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (marker < 0) goto done;
    close(marker);
    size_t have = 0;
    while (have < sizeof(seed.bytes)) {
        ssize_t n = read(fd, seed.bytes + have, sizeof(seed.bytes) - have);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) goto done;
        have += (size_t)n;
    }
    close(fd); fd = open(next, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0 || write(fd, seed.bytes, sizeof(seed.bytes)) != sizeof(seed.bytes) || fsync(fd) < 0) goto done;
    close(fd); fd = -1;
    if (rename(next, path) < 0 || fsync(directory) < 0) goto done;
    unlink(used);
    result = 0;
 done:
    if (fd >= 0) close(fd);
    if (directory >= 0) close(directory);
    if (consumed && !credited) rename(used, path);
    memset(seed.bytes, 0, sizeof(seed.bytes));
    close(lock); unlink(lock_path);
    return result;
#else
    return 0;
#endif
}
static volatile sig_atomic_t stopped;
static volatile sig_atomic_t child_pid;
static volatile sig_atomic_t collector_pid;
static void stop(int signal_number) {
    (void)signal_number; stopped = 1;
    if (child_pid > 0) kill((pid_t)child_pid, SIGTERM);
    if (collector_pid > 0) kill(-(pid_t)collector_pid, SIGTERM);
}
static pid_t start_collector(void) {
    pid_t child = fork();
    if (child == 0) {
        setsid();
        execl("/data/pixelctl/standalone", "pixelctl-standalone", "--config", "/data/pixelctl/standalone.json", (char *)NULL);
        _exit(127);
    }
    return child;
}
static void stop_collector(pid_t child) {
    if (child <= 0) return;
    kill(-child, SIGTERM);
    for (int i = 0; i < 20; i++) {
        if (waitpid(child, NULL, WNOHANG) == child) { kill(-child, SIGKILL); return; }
        struct timespec tick = {.tv_nsec = 100000000}; nanosleep(&tick, NULL);
    }
    kill(-child, SIGKILL); waitpid(child, NULL, 0);
}
int main(int argc, char **argv) {
    int seeded = seed_kernel();
    if (argc == 2 && !strcmp(argv[1], "--seed-only")) return seeded ? 1 : 0;
    if (seeded) fprintf(stderr, "pixelctl: secure entropy seed unavailable; native display still runs\n");
    if (argc != 6 && argc != 7) { fprintf(stderr, "Usage: supervisor GUI_PID DELAY_SECONDS BINARY CACHE_DIR TOKEN_FILE [ARGS_FILE]\n"); return 1; }
    pid_t gui = (pid_t)atoi(argv[1]);
    int delay = atoi(argv[2]);
    if (gui <= 1 || delay < 0 || delay > 120) return 1;
    char *arguments[80] = {argv[3], "--cache", argv[4], "--token-file", argv[5]};
    int count = 5;
    if (argc == 7) {
        FILE *file = fopen(argv[6], "r");
        if (file) {
            char line[256];
            while (fgets(line, sizeof(line), file)) {
                line[strcspn(line, "\r\n")] = 0;
                if (!line[0]) continue;
                if (count >= 79 || strlen(line) >= 254) { fclose(file); return 1; }
                arguments[count++] = strdup(line);
            }
            if (ferror(file)) { fclose(file); return 1; }
            fclose(file);
        }
    }
    arguments[count] = NULL;
    signal(SIGTERM, stop); signal(SIGINT, stop);
    struct timespec remaining = {.tv_sec = delay};
    while (!stopped && nanosleep(&remaining, &remaining) < 0 && errno == EINTR) {}
    if (stopped || kill(gui, 0) < 0) return 1;
    if (kill(gui, SIGSTOP) < 0) { perror("suspend vendor GUI"); return 1; }
    pid_t child = fork();
    if (child == 0) {
        execv(argv[3], arguments);
        perror("start native receiver"); _exit(127);
    }
    child_pid = child;
    int status = 1;
    if (child > 0) {
        time_t restart_after = 0;
        for (;;) {
            pid_t ended = waitpid(child, &status, WNOHANG);
            if (ended == child || (ended < 0 && errno != EINTR)) break;
            if (stopped) kill(child, SIGTERM);
            int standalone = !stopped && access("/data/pixelctl/standalone.json", R_OK) == 0 && access("/data/pixelctl/standalone", X_OK) == 0;
            if (collector_pid > 0) {
                pid_t reaped = waitpid((pid_t)collector_pid, NULL, WNOHANG);
                if (reaped == collector_pid) { kill(-(pid_t)collector_pid, SIGKILL); collector_pid = 0; restart_after = time(NULL) + 10; }
                else if (!standalone) { stop_collector((pid_t)collector_pid); collector_pid = 0; }
            }
            if (standalone && collector_pid == 0 && time(NULL) >= restart_after) {
                collector_pid = start_collector();
                if (collector_pid < 0) { collector_pid = 0; restart_after = time(NULL) + 10; }
            }
            struct timespec tick = {.tv_nsec = 250000000}; nanosleep(&tick, NULL);
        }
    }
    stop_collector((pid_t)collector_pid); collector_pid = 0; child_pid = 0;
    if (kill(gui, SIGCONT) < 0) perror("resume vendor GUI");
    fprintf(stderr, "Native receiver exited; vendor GUI resumed\n");
    return status == 0 ? 0 : 1;
}
