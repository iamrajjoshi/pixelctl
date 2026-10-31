/* SPDX-License-Identifier: GPL-3.0-or-later
 * pixelctl TC002 native display receiver.
 * Hardware protocol derived from UlanziTechnology/Ulanzi-U-Clock-TC002
 * Z21_TC002_Demo/src/pages/PageBase.cpp and managers/KeyManager.cpp.
 */
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <linux/input.h>
#include <linux/spi/spidev.h>
#endif

#define WIDTH 52
#define HEIGHT 16
#define FRAME_BYTES (WIDTH * HEIGHT * 3)
#define MAX_PAGES 32
static int page_count = 1;
#define PORT 9000
static char names[MAX_PAGES][33] = {"clock"};
static int static_pages[MAX_PAGES];
static char clock_page[33] = "clock";
static int clock_index, rotate_seconds = 8, stale_seconds = 180, mark_width;
static unsigned char clock_color[3] = {232, 232, 232}, date_color[3] = {150, 150, 150}, accent_color[3] = {180, 180, 180};
static unsigned char frames[MAX_PAGES][FRAME_BYTES];
static unsigned char display_frame[FRAME_BYTES];
static time_t updated[MAX_PAGES];
static int current, paused, spi_fd = -1, gpio_fd = -1, simulate, dirty = 1;
static int brightness = 40;
static int64_t next_rotation;
static unsigned long displayed, received;
static volatile sig_atomic_t running = 1;
static char token[129];
static const char *cache_dir;
struct glyph { unsigned char character, width, height, rows[7]; };
#include "font.h"

static void pixel(unsigned char *frame, int x, int y, unsigned char r, unsigned char g, unsigned char b) {
    if (x < 0 || x >= WIDTH || y < 0 || y >= HEIGHT) return;
    int i = (y * WIDTH + x) * 3;
    frame[i] = r; frame[i + 1] = g; frame[i + 2] = b;
}
static void text(unsigned char *frame, const char *value, int x, int y, int large, unsigned char r, unsigned char g, unsigned char b) {
    const struct glyph *glyphs = large ? big : font;
    size_t count = large ? sizeof(big) / sizeof(big[0]) : sizeof(font) / sizeof(font[0]);
    for (const unsigned char *p = (const unsigned char *)value; *p; p++) {
        unsigned char c = *p >= 'a' && *p <= 'z' ? *p - 'a' + 'A' : *p;
        for (size_t i = 0; i < count; i++) if (glyphs[i].character == c) {
            const struct glyph *glyph = &glyphs[i];
            for (int row = 0; row < glyph->height; row++) for (int col = 0; col < glyph->width; col++) {
                if (glyph->rows[row] & (1 << (glyph->width - col - 1))) pixel(frame, x + col, y + row, r, g, b);
            }
            x += glyph->width + 1; break;
        }
    }
}
static void compose_frame(void) {
    memcpy(display_frame, frames[current], FRAME_BYTES);
    time_t now = time(NULL);
    if (current == clock_index && (!updated[current] || now - updated[current] > 10)) {
        for (int y = 0; y < HEIGHT; y++) for (int x = mark_width ? mark_width + 1 : 0; x < WIDTH; x++) pixel(display_frame, x, y, 0, 0, 0);
        struct tm local; localtime_r(&now, &local);
        char value[32]; strftime(value, sizeof(value), "%H:%M", &local);
        text(display_frame, value, mark_width ? mark_width + 4 : 13, 1, 1, clock_color[0], clock_color[1], clock_color[2]);
        strftime(value, sizeof(value), "%a %m/%d", &local);
        text(display_frame, value, mark_width ? mark_width + 4 : 9, 10, 0, date_color[0], date_color[1], date_color[2]);
        for (int x = 0; x < (mark_width ? mark_width : WIDTH); x++) pixel(display_frame, x, 15, 0, 0, 0);
        pixel(display_frame, local.tm_sec % (mark_width ? mark_width : WIDTH), 15, accent_color[0], accent_color[1], accent_color[2]);
    } else if (!static_pages[current] && current != clock_index && (!updated[current] || now - updated[current] > stale_seconds)) {
        for (int x = 0; x < WIDTH; x++) pixel(display_frame, x, 15, 235, 184, 93);
    }
    for (int i = 0; i < FRAME_BYTES; i++) display_frame[i] = (unsigned char)(display_frame[i] * brightness / 100);
}

static int64_t monotonic_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static void stop_running(int sig) { (void)sig; running = 0; }
static int page_index(const char *name) {
    for (int i = 0; i < page_count; i++) if (!strcmp(name, names[i])) return i;
    return -1;
}
static int valid_id(const char *name) {
    if (!name[0] || strlen(name) > 32) return 0;
    for (const unsigned char *p = (const unsigned char *)name; *p; p++) {
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') || (*p >= '0' && *p <= '9') || *p == '_' || *p == '-')) return 0;
    }
    return 1;
}
static int load_pages(const char *path) {
    FILE *file = fopen(path, "r");
    if (!file) { perror("pages file"); return -1; }
    char line[80]; page_count = 0;
    while (fgets(line, sizeof(line), file)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!line[0]) continue;
        if (!valid_id(line) || page_count >= MAX_PAGES || page_index(line) >= 0) {
            fprintf(stderr, "Invalid, duplicate or excessive page ID\n"); fclose(file); return -1;
        }
        strcpy(names[page_count++], line);
    }
    int failed = ferror(file); fclose(file);
    if (failed || !page_count) { fprintf(stderr, "Cannot read nonempty page list\n"); return -1; }
    return 0;
}
static int parse_color(const char *value, unsigned char color[3]) {
    if (*value == '#') value++;
    unsigned int r, g, b;
    if (strlen(value) != 6 || sscanf(value, "%2x%2x%2x", &r, &g, &b) != 3) return -1;
    color[0] = r; color[1] = g; color[2] = b; return 0;
}
static void choose_page(int index) {
    current = (index + page_count) % page_count;
    next_rotation = monotonic_ms() + rotate_seconds * 1000;
    dirty = 1;
}
static void gpio_value(char value) {
    if (gpio_fd >= 0) {
        lseek(gpio_fd, 0, SEEK_SET);
        if (write(gpio_fd, &value, 1) != 1) perror("GPIO35 write");
    }
}
static int hardware_init(void) {
    if (simulate) return 0;
#ifdef __linux__
    spi_fd = open("/dev/spidev0.0", O_WRONLY | O_CLOEXEC);
    gpio_fd = open("/sys/class/gpio/gpio35/value", O_WRONLY | O_CLOEXEC);
    if (spi_fd < 0 || gpio_fd < 0) { perror("open SPI/GPIO"); return -1; }
    uint8_t mode = SPI_MODE_0, bits = 8;
    uint32_t speed = 10000000;
    if (ioctl(spi_fd, SPI_IOC_WR_MODE, &mode) < 0 || ioctl(spi_fd, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0 || ioctl(spi_fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed) < 0) {
        perror("configure SPI"); return -1;
    }
    return 0;
#else
    fprintf(stderr, "Hardware mode requires Linux; use --simulate.\n");
    return -1;
#endif
}
static int write_frame(void) {
    static int64_t last_frame;
    compose_frame();
    if (simulate) { displayed++; dirty = 0; return 0; }
    int64_t elapsed = monotonic_ms() - last_frame;
    if (elapsed < 15) usleep((useconds_t)(15 - elapsed) * 1000);
    unsigned char padded[64 * HEIGHT * 3] = {0};
    for (int y = 0; y < HEIGHT; y++) memcpy(padded + y * 64 * 3, display_frame + y * WIDTH * 3, WIDTH * 3);
    gpio_value('0');
    usleep(1000);
    ssize_t n = write(spi_fd, padded, sizeof(padded));
    gpio_value('1');
    last_frame = monotonic_ms();
    if (n != sizeof(padded)) { perror("SPI frame"); return -1; }
    displayed++; dirty = 0;
    return 0;
}
static void cache_load(void) {
    if (!cache_dir) return;
    for (int i = 0; i < page_count; i++) {
        char path[1024]; snprintf(path, sizeof(path), "%s/%s.rgb", cache_dir, names[i]);
        FILE *f = fopen(path, "rb");
        if (!f) continue;
        if (fread(frames[i], 1, FRAME_BYTES, f) != FRAME_BYTES) memset(frames[i], 0, FRAME_BYTES);
        fclose(f);
    }
}
static void cache_save(int i) {
    if (!cache_dir) return;
    /* Only explicit /persist requests write flash. Frequent live frames stay in RAM. */
    char path[1024]; snprintf(path, sizeof(path), "%s/%s.rgb", cache_dir, names[i]);
    FILE *f = fopen(path, "wb");
    if (!f) return;
    if (fwrite(frames[i], 1, FRAME_BYTES, f) != FRAME_BYTES) perror("cache frame");
    fclose(f);
}
static int send_all(int fd, const void *data, size_t size) {
    const unsigned char *p = data;
    while (size) {
        ssize_t n = send(fd, p, size, 0);
        if (n <= 0) return -1;
        p += n; size -= (size_t)n;
    }
    return 0;
}
static void reply(int fd, int code, const char *type, const void *body, size_t size) {
    char head[256];
    int n = snprintf(head, sizeof(head), "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %zu\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n", code, code == 200 ? "OK" : "Error", type, size);
    if (send_all(fd, head, (size_t)n) == 0) send_all(fd, body, size);
}
static void json_reply(int fd, int code, const char *body) { reply(fd, code, "application/json", body, strlen(body)); }
static ssize_t receive_until(int fd, void *buffer, size_t size, int64_t deadline) {
    for (;;) {
        int64_t remaining = deadline - monotonic_ms();
        if (remaining <= 0) { errno = ETIMEDOUT; return -1; }
        struct pollfd input = {.fd = fd, .events = POLLIN};
        int ready = poll(&input, 1, (int)remaining);
        if (ready < 0 && errno == EINTR) continue;
        if (ready <= 0) return -1;
        ssize_t n = recv(fd, buffer, size, 0);
        if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        return n;
    }
}
static void client_request(int client) {
    char buffer[8192 + FRAME_BYTES];
    size_t have = 0, header_len = 0;
    int64_t deadline = monotonic_ms() + 1000;
    struct timeval timeout = {.tv_sec = 1, .tv_usec = 0};
    setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    while (!header_len && have < 8192) {
        ssize_t n = receive_until(client, buffer + have, 8192 - have, deadline);
        if (n <= 0) return;
        have += (size_t)n; buffer[have] = 0;
        char *end = strstr(buffer, "\r\n\r\n");
        if (end) header_len = (size_t)(end - buffer) + 4;
    }
    if (!header_len || header_len > 8192) { json_reply(client, 400, "{\"error\":\"header too long\"}"); return; }
    char method[16], path[128];
    if (sscanf(buffer, "%15s %127s", method, path) != 2) return;
    size_t length = 0;
    int authorized = token[0] == 0;
    for (char *line = strstr(buffer, "\r\n"); line && line < buffer + header_len - 2; ) {
        line += 2;
        char *end = strstr(line, "\r\n");
        if (!end) break;
        char saved = *end; *end = 0;
        if (!strncasecmp(line, "Content-Length:", 15)) {
            char *tail = NULL;
            unsigned long parsed = strtoul(line + 15, &tail, 10);
            if (tail == line + 15 || parsed > FRAME_BYTES) { *end = saved; json_reply(client, 400, "{\"error\":\"invalid body length\"}"); return; }
            length = parsed;
        }
        if (!strncasecmp(line, "X-Pixelctl-Key:", 15)) {
            const char *value = strchr(line, ':') + 1; while (*value == ' ') value++;
            authorized = strcmp(value, token) == 0;
        }
        *end = saved; line = end;
    }
    if (header_len + length > sizeof(buffer)) { json_reply(client, 400, "{\"error\":\"body too long\"}"); return; }
    if (!authorized) { json_reply(client, 403, "{\"error\":\"unauthorized\"}"); return; }
    if (!strcmp(method, "GET") && !strcmp(path, "/status")) {
        char status[4096];
        int n = snprintf(status, sizeof(status), "{\"native\":true,\"page\":\"%s\",\"index\":%d,\"paused\":%s,\"brightness\":%d,\"time\":%lld,\"uptime\":%lld,\"frames_displayed\":%lu,\"frames_received\":%lu,\"updated\":[", names[current], current, paused ? "true" : "false", brightness, (long long)time(NULL), (long long)(monotonic_ms() / 1000), displayed, received);
        for (int i = 0; i < page_count; i++) n += snprintf(status + n, sizeof(status) - (size_t)n, "%s%lld", i ? "," : "", (long long)updated[i]);
        n += snprintf(status + n, sizeof(status) - (size_t)n, "],\"pages\":[");
        for (int i = 0; i < page_count; i++) n += snprintf(status + n, sizeof(status) - (size_t)n, "%s\"%s\"", i ? "," : "", names[i]);
        snprintf(status + n, sizeof(status) - (size_t)n, "]}");
        json_reply(client, 200, status); return;
    }
    if (!strcmp(method, "GET") && !strcmp(path, "/display")) {
        compose_frame(); reply(client, 200, "application/octet-stream", display_frame, FRAME_BYTES); return;
    }
    while (have < header_len + length) {
        ssize_t n = receive_until(client, buffer + have, header_len + length - have, deadline);
        if (n <= 0) return;
        have += (size_t)n;
    }
    if (!strncmp(path, "/frame/", 7)) {
        int i = page_index(path + 7);
        if (i < 0) { json_reply(client, 404, "{\"error\":\"unknown page\"}"); return; }
        if (!strcmp(method, "GET")) { reply(client, 200, "application/octet-stream", frames[i], FRAME_BYTES); return; }
        if (!strcmp(method, "POST") && length == FRAME_BYTES) {
            memcpy(frames[i], buffer + header_len, FRAME_BYTES);
            updated[i] = time(NULL); received++;
            if (i == current) dirty = 1;
            json_reply(client, 200, "{\"ok\":true}"); return;
        }
        json_reply(client, 400, "{\"error\":\"expected 2496 RGB bytes\"}"); return;
    }
    if (!strcmp(method, "POST") && !strncmp(path, "/page/", 6)) {
        int i = page_index(path + 6);
        if (i < 0) { json_reply(client, 404, "{\"error\":\"unknown page\"}"); return; }
        choose_page(i); json_reply(client, 200, "{\"ok\":true}"); return;
    }
    if (!strcmp(method, "POST") && !strcmp(path, "/pause")) {
        paused = !paused; next_rotation = monotonic_ms() + rotate_seconds * 1000;
        json_reply(client, 200, paused ? "{\"paused\":true}" : "{\"paused\":false}"); return;
    }
    if (!strcmp(method, "POST") && !strcmp(path, "/persist")) {
        for (int i = 0; i < page_count; i++) cache_save(i);
        json_reply(client, 200, "{\"ok\":true}"); return;
    }
    json_reply(client, 404, "{\"error\":\"not found\"}");
}
static void read_key(int fd) {
#ifdef __linux__
    static unsigned int last_knob;
    struct input_event event;
    while (read(fd, &event, sizeof(event)) == sizeof(event)) {
        if (event.type == EV_ABS) {
            if (event.value == 1 && last_knob == 8) choose_page(current + 1);
            if (event.value == 11 && last_knob == 13) choose_page(current - 1);
            if (event.value == 8 || event.value == 13 || event.value == 1 || event.value == 11) last_knob = event.value;
        } else if (event.type == EV_KEY && event.value == 1) {
            if (event.code == 0x67) { paused = !paused; next_rotation = monotonic_ms() + rotate_seconds * 1000; }
            if (event.code == 0x6c) choose_page(current - 1);
            if (event.code == 0x6a) choose_page(current + 1);
            if (event.code == 0x69) choose_page(clock_index >= 0 ? clock_index : 0);
        }
    }
#else
    (void)fd;
#endif
}
int main(int argc, char **argv) {
    int port = PORT;
    const char *pages_file = NULL;
    char requested_static[MAX_PAGES][33]; int static_count = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--simulate")) simulate = 1;
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--cache") && i + 1 < argc) cache_dir = argv[++i];
        else if (!strcmp(argv[i], "--pages-file") && i + 1 < argc) pages_file = argv[++i];
        else if (!strcmp(argv[i], "--timezone") && i + 1 < argc) setenv("TZ", argv[++i], 1);
        else if (!strcmp(argv[i], "--clock-page") && i + 1 < argc) {
            const char *id = argv[++i]; if (!valid_id(id)) return 1; strcpy(clock_page, id);
        }
        else if (!strcmp(argv[i], "--static-page") && i + 1 < argc) {
            const char *id = argv[++i];
            if (!valid_id(id) || static_count >= MAX_PAGES) return 1;
            strcpy(requested_static[static_count++], id);
        }
        else if (!strcmp(argv[i], "--rotate") && i + 1 < argc) {
            rotate_seconds = atoi(argv[++i]); if (rotate_seconds < 1 || rotate_seconds > 3600) return 1;
        }
        else if (!strcmp(argv[i], "--stale-after") && i + 1 < argc) {
            stale_seconds = atoi(argv[++i]); if (stale_seconds < 10 || stale_seconds > 86400) return 1;
        }
        else if (!strcmp(argv[i], "--clock-mark-width") && i + 1 < argc) {
            mark_width = atoi(argv[++i]); if (mark_width < 0 || mark_width > 14) return 1;
        }
        else if (!strcmp(argv[i], "--clock-color") && i + 1 < argc) { if (parse_color(argv[++i], clock_color)) return 1; }
        else if (!strcmp(argv[i], "--date-color") && i + 1 < argc) { if (parse_color(argv[++i], date_color)) return 1; }
        else if (!strcmp(argv[i], "--accent-color") && i + 1 < argc) { if (parse_color(argv[++i], accent_color)) return 1; }
        else if (!strcmp(argv[i], "--brightness") && i + 1 < argc) {
            brightness = atoi(argv[++i]);
            if (brightness < 5 || brightness > 100) { fprintf(stderr, "Brightness must be 5-100\n"); return 1; }
        }
        else if (!strcmp(argv[i], "--token-file") && i + 1 < argc) {
            FILE *f = fopen(argv[++i], "r");
            if (!f || !fgets(token, sizeof(token), f)) { perror("token file"); return 1; }
            fclose(f); token[strcspn(token, "\r\n")] = 0;
            if (!token[0]) { fprintf(stderr, "Empty token refused\n"); return 1; }
        } else { fprintf(stderr, "Unknown argument: %s\n", argv[i]); return 1; }
    }
    if (pages_file && load_pages(pages_file)) return 1;
    clock_index = page_index(clock_page);
    for (int i = 0; i < static_count; i++) {
        int index = page_index(requested_static[i]);
        if (index < 0) { fprintf(stderr, "Unknown static page\n"); return 1; }
        static_pages[index] = 1;
    }
    signal(SIGINT, stop_running); signal(SIGTERM, stop_running); signal(SIGPIPE, SIG_IGN);
    if (!simulate && !token[0]) { fprintf(stderr, "Hardware mode requires --token-file\n"); return 1; }
    if (!getenv("TZ")) setenv("TZ", "UTC0", 1); tzset();
    if (hardware_init() < 0) return 1;
    cache_load();
    int server = socket(AF_INET, SOCK_STREAM, 0), yes = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in addr = {.sin_family = AF_INET, .sin_port = htons((uint16_t)port), .sin_addr = {.s_addr = htonl(INADDR_ANY)}};
    if (bind(server, (struct sockaddr *)&addr, sizeof(addr)) < 0 || listen(server, 4) < 0) { perror("listen"); return 1; }
    struct pollfd fds[3] = {{.fd = server, .events = POLLIN}, {.fd = -1, .events = POLLIN}, {.fd = -1, .events = POLLIN}};
    if (!simulate) {
        fds[1].fd = open("/dev/input/event67", O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        fds[2].fd = open("/dev/input/event68", O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fds[1].fd < 0 || fds[2].fd < 0) { perror("open hardware buttons"); return 1; }
    }
    choose_page(0);
    fprintf(stderr, "pixelctl native receiver ready on port %d (simulate=%d)\n", port, simulate);
    time_t last_tick = 0;
    while (running) {
        int ready = poll(fds, 3, 50);
        if (ready > 0) {
            if (fds[0].revents & POLLIN) {
                int client = accept(server, NULL, NULL);
                if (client >= 0) { client_request(client); close(client); }
            }
            for (int i = 1; i < 3; i++) if (fds[i].revents & POLLIN) read_key(fds[i].fd);
        }
        if (!paused && monotonic_ms() >= next_rotation) choose_page(current + 1);
        time_t tick = time(NULL);
        if (tick != last_tick) { dirty = 1; last_tick = tick; }
        if (dirty && write_frame() < 0) break;
    }
    for (int i = 0; i < 3; i++) if (fds[i].fd >= 0) close(fds[i].fd);
    if (spi_fd >= 0) close(spi_fd); if (gpio_fd >= 0) close(gpio_fd);
    return 0;
}
