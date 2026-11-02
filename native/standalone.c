/* SPDX-License-Identifier: GPL-3.0-or-later
 * On-device provider worker and renderer. Cloud credentials stay in device
 * files; only RGB frames and display control travel over authenticated loopback.
 */
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "json.h"
#include "standalone_pages.h"
#include <arpa/inet.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

#define MAX_PAGES 32
typedef struct {
    pc_config provider;
    char pages_path[512], key_path[512], uptime_path[512], status_path[512];
    char pages[MAX_PAGES][33], key[129];
    int page_count, refresh_seconds;
} engine_config;
typedef struct {
    engine_config config;
    pc_state *state;
    pthread_mutex_t mutex;
    pthread_cond_t wake;
    int stopping;
    unsigned long cycles, health_cycles;
    time_t last_collect, started;
} engine;
static volatile sig_atomic_t running = 1;
static void stop_running(int signal_number) { (void)signal_number; running = 0; }
static char *read_file(const char *path, size_t limit) {
    FILE *file = fopen(path, "rb"); if (!file) return NULL;
    char *text = malloc(limit + 1); if (!text) { fclose(file); return NULL; }
    size_t n = fread(text, 1, limit, file);
    int extra = fgetc(file), failed = ferror(file); fclose(file);
    if (failed || extra != EOF) { free(text); return NULL; }
    text[n] = 0; return text;
}
static void setting(const pc_json *json, const char *key, char *out, size_t capacity) {
    int token = pc_json_get(json, 0, key);
    if (token >= 0) pc_json_string(json, token, out, capacity);
}
static int config_load(const char *path, engine_config *config) {
    memset(config, 0, sizeof(*config));
    char error[160];
    if (pc_provider_config_load(path, &config->provider, error, sizeof(error))) { fprintf(stderr, "Standalone config: %s\n", error); return -1; }
    strcpy(config->pages_path, "/data/pixelctl/pages"); strcpy(config->key_path, "/data/pixelctl.key");
    strcpy(config->uptime_path, "/data/pixelctl/uptime.state"); strcpy(config->status_path, "/tmp/pixelctl-standalone.status.json");
    char *text = read_file(path, 65536); pc_json json;
    if (!text || pc_json_parse(&json, text) < 0) { free(text); return -1; }
    setting(&json, "pages_file", config->pages_path, sizeof(config->pages_path));
    setting(&json, "key_file", config->key_path, sizeof(config->key_path));
    setting(&json, "uptime_file", config->uptime_path, sizeof(config->uptime_path));
    setting(&json, "status_file", config->status_path, sizeof(config->status_path));
    config->refresh_seconds = (int)pc_json_integer(&json, pc_json_get(&json, 0, "refresh_seconds"), 60);
    pc_json_free(&json); free(text);
    if (config->refresh_seconds < 15 || config->refresh_seconds > 3600) return -1;
    FILE *pages = fopen(config->pages_path, "r"); if (!pages) return -1;
    char line[80];
    while (fgets(line, sizeof(line), pages)) {
        line[strcspn(line, "\r\n")] = 0; if (!line[0]) continue;
        if (strlen(line) > 32 || config->page_count >= MAX_PAGES) { fclose(pages); return -1; }
        int known = 0;
        for (size_t i = 0; i < pc_builtin_page_count; i++) if (!strcmp(line, pc_builtin_pages[i])) known = 1;
        for (int i = 0; i < config->page_count; i++) if (!strcmp(line, config->pages[i])) known = 0;
        if (!known) { fclose(pages); fprintf(stderr, "Unknown or duplicate built-in page ID\n"); return -1; }
        strcpy(config->pages[config->page_count++], line);
    }
    int failed = ferror(pages); fclose(pages);
    if (failed || !config->page_count) return -1;
    return 0;
}
static int key_load(engine_config *config) {
    char *key = read_file(config->key_path, 128);
    if (!key) return -1;
    key[strcspn(key, "\r\n")] = 0;
    if (!key[0]) { free(key); return -1; }
    for (const unsigned char *p = (const unsigned char *)key; *p; p++) if (*p <= 32 || *p >= 127) { free(key); return -1; }
    strcpy(config->key, key); free(key); return 0;
}
static uint32_t checksum(const unsigned char *bytes, size_t size) {
    uint32_t crc = 0xffffffffU;
    for (size_t i = 0; i < size; i++) {
        crc ^= bytes[i];
        for (int bit = 0; bit < 8; bit++) crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1)));
    }
    return ~crc;
}
typedef struct {
    char magic[8]; uint32_t size, crc;
    pc_uptime uptime;
    int history[PC_HISTORY], history_count;
} observations;
static void observations_load(const char *path, pc_state *state) {
    FILE *file = fopen(path, "rb"); if (!file) return;
    observations saved; memset(&saved, 0, sizeof(saved));
    size_t n = fread(&saved, 1, sizeof(saved), file); int extra = fgetc(file); fclose(file);
    uint32_t crc = saved.crc; saved.crc = 0;
    if (n != sizeof(saved) || extra != EOF || memcmp(saved.magic, "PCOBS1", 6) || saved.size != sizeof(saved) || crc != checksum((const unsigned char *)&saved, sizeof(saved))) return;
    if (saved.history_count < 0 || saved.history_count > PC_HISTORY || saved.uptime.failures > saved.uptime.samples) return;
    state->uptime = saved.uptime; state->health.history_count = saved.history_count;
    memcpy(state->health.history, saved.history, sizeof(saved.history));
}
static void observations_save(const char *path, const pc_state *state) {
    observations saved; memset(&saved, 0, sizeof(saved)); memcpy(saved.magic, "PCOBS1", 6); saved.size = sizeof(saved);
    saved.uptime = state->uptime; saved.history_count = state->health.history_count; memcpy(saved.history, state->health.history, sizeof(saved.history));
    saved.crc = checksum((const unsigned char *)&saved, sizeof(saved));
    char next[560]; if (snprintf(next, sizeof(next), "%s.new", path) >= (int)sizeof(next)) return;
    int fd = open(next, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) return;
    int ok = write(fd, &saved, sizeof(saved)) == sizeof(saved) && fsync(fd) == 0; close(fd);
    if (ok) rename(next, path); else unlink(next);
}
typedef struct { engine *context; int health; } worker_argument;
static void *provider_worker(void *argument) {
    worker_argument *job = argument; engine *context = job->context;
    pc_state *working = malloc(sizeof(*working)); if (!working) return NULL;
    for (;;) {
        pthread_mutex_lock(&context->mutex);
        int stop = context->stopping; memcpy(working, context->state, sizeof(*working)); pthread_mutex_unlock(&context->mutex);
        if (stop) break;
        if (job->health) {
            pc_probe_health(&context->config.provider, working);
            observations_save(context->config.uptime_path, working);
        } else pc_collect_cloud(&context->config.provider, working);
        pthread_mutex_lock(&context->mutex);
        if (job->health) {
            context->state->health = working->health; context->state->uptime = working->uptime;
            context->health_cycles++;
        } else {
            context->state->prs = working->prs; context->state->garden = working->garden;
            context->state->deploy = working->deploy; context->cycles++; context->last_collect = time(NULL);
        }
        struct timespec until = {.tv_sec = time(NULL) + context->config.refresh_seconds};
        if (!context->stopping) pthread_cond_timedwait(&context->wake, &context->mutex, &until);
        stop = context->stopping; pthread_mutex_unlock(&context->mutex);
        if (stop) break;
    }
    free(working); return NULL;
}
static int send_all(int fd, const void *data, size_t size) {
    const unsigned char *p = data;
    while (size) { ssize_t n = send(fd, p, size, 0); if (n <= 0) return -1; p += n; size -= (size_t)n; }
    return 0;
}
static int loopback(const engine_config *config, const char *method, const char *path, const void *body, size_t size, char *response, size_t capacity) {
    int fd = socket(AF_INET, SOCK_STREAM, 0); if (fd < 0) return -1;
    struct timeval timeout = {.tv_sec = 0, .tv_usec = 400000};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)); setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    struct sockaddr_in address = {.sin_family = AF_INET, .sin_port = htons(9000), .sin_addr = {.s_addr = htonl(0x7f000001U)}};
    if (connect(fd, (struct sockaddr *)&address, sizeof(address))) { close(fd); return -1; }
    char header[512];
    int n = snprintf(header, sizeof(header), "%s %s HTTP/1.0\r\nHost: 127.0.0.1\r\nX-Pixelctl-Key: %s\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n", method, path, config->key, size);
    if (n < 0 || (size_t)n >= sizeof(header) || send_all(fd, header, (size_t)n) || (size && send_all(fd, body, size))) { close(fd); return -1; }
    char buffer[8192]; size_t have = 0;
    while (have < sizeof(buffer) - 1) {
        ssize_t read_size = recv(fd, buffer + have, sizeof(buffer) - have - 1, 0);
        if (read_size == 0) break;
        if (read_size < 0) { close(fd); return -1; }
        have += (size_t)read_size;
    }
    close(fd); buffer[have] = 0;
    int status = 0; char *payload = strstr(buffer, "\r\n\r\n");
    if (!payload || sscanf(buffer, "HTTP/%*d.%*d %d", &status) != 1 || status != 200) return -1;
    payload += 4;
    if (response && capacity) {
        size_t length = have - (size_t)(payload - buffer);
        if (length >= capacity) return -1;
        memcpy(response, payload, length); response[length] = 0;
    }
    return 0;
}
static void status_save(const engine *context, const pc_state *state, unsigned long sent, int receiver_ok, unsigned long cycles, unsigned long health_cycles, time_t collected) {
    char path[560]; if (snprintf(path, sizeof(path), "%s.new", context->config.status_path) >= (int)sizeof(path)) return;
    char prs_error[PC_ERROR * 6 + 3], deploy_error[PC_ERROR * 6 + 3], health_error[PC_ERROR * 6 + 3], garden_error[PC_ERROR * 6 + 3], serving_error[PC_ERROR * 6 + 3];
    pc_json_escape(state->prs.error, prs_error, sizeof(prs_error)); pc_json_escape(state->deploy.error, deploy_error, sizeof(deploy_error));
    pc_json_escape(state->health.error, health_error, sizeof(health_error)); pc_json_escape(state->garden.error, garden_error, sizeof(garden_error));
    pc_json_escape(state->deploy.serving_error, serving_error, sizeof(serving_error));
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600); if (fd < 0) return;
    FILE *file = fdopen(fd, "w"); if (!file) { close(fd); return; }
    fprintf(file, "{\"mode\":\"standalone\",\"pid\":%ld,\"started_at\":%lld,\"checked_at\":%lld,\"provider_cycles\":%lu,\"last_collect\":%lld,\"refresh_seconds\":%d,\"frames_sent\":%lu,\"receiver_ok\":%s", (long)getpid(), (long long)context->started, (long long)time(NULL), cycles, (long long)collected, context->config.refresh_seconds, sent, receiver_ok ? "true" : "false");
    fprintf(file, ",\"health_cycles\":%lu", health_cycles);
    fprintf(file, ",\"prs\":{\"count\":%d,\"total\":%d,\"complete\":%s,\"checks_available\":%s,\"updated_at\":%lld,\"error\":%s}", state->prs.count, state->prs.total, state->prs.complete ? "true" : "false", state->prs.checks_available ? "true" : "false", (long long)state->prs.updated_at, prs_error);
    fprintf(file, ",\"garden\":{\"days\":%d,\"total\":%d,\"updated_at\":%lld,\"error\":%s}", state->garden.count, state->garden.total, (long long)state->garden.updated_at, garden_error);
    fprintf(file, ",\"deploy\":{\"state\":%d,\"live\":%s,\"updated_at\":%lld,\"error\":%s,\"serving_error\":%s,\"serving_verified_at\":%lld}", state->deploy.state, state->deploy.live ? "true" : "false", (long long)state->deploy.updated_at, deploy_error, serving_error, (long long)state->deploy.serving_verified_at);
    fprintf(file, ",\"health\":{\"state\":%d,\"latency_ms\":%d,\"updated_at\":%lld,\"error\":%s}", state->health.state, state->health.latency_ms, (long long)state->health.updated_at, health_error);
    fprintf(file, ",\"uptime\":{\"samples\":%llu,\"failures\":%llu,\"unknown\":%llu,\"availability_pct\":%.5f}}\n", (unsigned long long)state->uptime.samples, (unsigned long long)state->uptime.failures, (unsigned long long)state->uptime.unknown, state->uptime.availability_pct);
    fclose(file); rename(path, context->config.status_path);
}
int main(int argc, char **argv) {
    const char *config_path = "/data/pixelctl/standalone.json", *render_dir = NULL; int once = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--config") && i + 1 < argc) config_path = argv[++i];
        else if (!strcmp(argv[i], "--render-dir") && i + 1 < argc) render_dir = argv[++i];
        else if (!strcmp(argv[i], "--once")) once = 1;
        else { fprintf(stderr, "Usage: standalone --config FILE [--once --render-dir DIR]\n"); return 1; }
    }
    engine *context = calloc(1, sizeof(*context)); pc_state *view = calloc(1, sizeof(*view));
    if (!context || !view || config_load(config_path, &context->config)) { fprintf(stderr, "Invalid standalone configuration\n"); return 1; }
    context->state = calloc(1, sizeof(*context->state)); if (!context->state) return 1;
    context->state->uptime.availability_pct = -1;
    for (int i = 0; i < PC_HISTORY; i++) context->state->health.history[i] = -1;
    observations_load(context->config.uptime_path, context->state);
    setenv("TZ", context->config.provider.timezone[0] ? context->config.provider.timezone : "UTC0", 1); tzset();
    signal(SIGTERM, stop_running); signal(SIGINT, stop_running); signal(SIGPIPE, SIG_IGN);
    context->started = time(NULL); pthread_mutex_init(&context->mutex, NULL); pthread_cond_init(&context->wake, NULL);
    pthread_t worker, health_worker; worker_argument cloud_job = {context, 0}, health_job = {context, 1};
    if (once) { pc_collect(&context->config.provider, context->state); context->cycles = 1; context->health_cycles = 1; context->last_collect = time(NULL); }
    else {
        pthread_attr_t attributes; pthread_attr_init(&attributes); pthread_attr_setstacksize(&attributes, 512 * 1024);
        int error = pthread_create(&worker, &attributes, provider_worker, &cloud_job);
        if (!error) error = pthread_create(&health_worker, &attributes, provider_worker, &health_job);
        pthread_attr_destroy(&attributes);
        if (error) { fprintf(stderr, "Cannot start provider worker\n"); return 1; }
    }
    if (!render_dir && key_load(&context->config)) { fprintf(stderr, "Cannot load private receiver key\n"); return 1; }
    double phases[MAX_PAGES] = {0}; unsigned long sent = 0; time_t persist_at = time(NULL) + 60;
    char selected[33] = "clock"; struct timespec previous; clock_gettime(CLOCK_MONOTONIC, &previous);
    do {
        struct timespec now_tick; clock_gettime(CLOCK_MONOTONIC, &now_tick);
        double elapsed = (now_tick.tv_sec - previous.tv_sec) + (now_tick.tv_nsec - previous.tv_nsec) / 1000000000.0; previous = now_tick;
        char response[4096]; int receiver_ok = render_dir || !loopback(&context->config, "GET", "/status", NULL, 0, response, sizeof(response));
        if (receiver_ok && !render_dir) {
            pc_json status;
            if (pc_json_parse(&status, response) >= 0) { pc_json_string(&status, pc_json_get(&status, 0, "page"), selected, sizeof(selected)); pc_json_free(&status); }
        }
        pthread_mutex_lock(&context->mutex); memcpy(view, context->state, sizeof(*view)); unsigned long cycles = context->cycles, health_cycles = context->health_cycles; time_t collected = context->last_collect; pthread_mutex_unlock(&context->mutex);
        for (int i = 0; i < context->config.page_count; i++) {
            if (!strcmp(selected, context->config.pages[i])) phases[i] += elapsed;
            unsigned char frame[PC_FRAME_BYTES];
            if (pc_render_page(context->config.pages[i], view, &context->config.provider, phases[i], time(NULL), frame)) continue;
            if (render_dir) {
                char path[1024]; snprintf(path, sizeof(path), "%s/%s.rgb", render_dir, context->config.pages[i]); FILE *file = fopen(path, "wb"); if (file) { fwrite(frame, 1, sizeof(frame), file); fclose(file); sent++; }
            } else {
                char path[80]; snprintf(path, sizeof(path), "/frame/%s", context->config.pages[i]);
                if (!loopback(&context->config, "POST", path, frame, sizeof(frame), NULL, 0)) sent++;
            }
        }
        if (!render_dir && time(NULL) >= persist_at) { if (!loopback(&context->config, "POST", "/persist", NULL, 0, NULL, 0)) persist_at = time(NULL) + 3600; }
        status_save(context, view, sent, receiver_ok, cycles, health_cycles, collected);
        if (once) break;
        struct timespec delay = {.tv_sec = 1}; nanosleep(&delay, NULL);
    } while (running);
    if (!once) {
        pthread_mutex_lock(&context->mutex); context->stopping = 1; pthread_cond_broadcast(&context->wake); pthread_mutex_unlock(&context->mutex); pthread_join(worker, NULL); pthread_join(health_worker, NULL);
    }
    free(context->state); free(view); free(context); return 0;
}
