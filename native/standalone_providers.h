/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef PIXELCTL_STANDALONE_PROVIDERS_H
#define PIXELCTL_STANDALONE_PROVIDERS_H

#include <stddef.h>
#include <stdint.h>
#include <time.h>

#define PC_MAX_PRS 1000
#define PC_GARDEN_DAYS 371
#define PC_HISTORY 52
#define PC_ERROR 96

/* Unknown is deliberately zero: missing credentials and unobserved probes
 * must never initialize to success. */
enum pc_status { PC_UNKNOWN, PC_OK, PC_BUILDING, PC_FAILED, PC_CANCELLED };
enum pc_review { PC_REVIEW_UNKNOWN, PC_REVIEW_REQUIRED, PC_REVIEW_APPROVED, PC_REVIEW_CHANGES };

typedef struct {
    int number, draft, status, review;
    char title[256], repo[128];
    time_t updated_at;
} pc_pr;

typedef struct {
    pc_pr items[PC_MAX_PRS];
    int count, total, complete, checks_available;
    time_t updated_at;
    char error[PC_ERROR];
} pc_pr_list;

typedef struct { char date[11]; int count; } pc_contribution;
typedef struct {
    pc_contribution days[PC_GARDEN_DAYS];
    int count, total;
    time_t updated_at;
    char error[PC_ERROR];
} pc_garden;

typedef struct {
    int state, serving_state, live;
    char sha[65], serving_sha[65], url[256], serving_url[256];
    time_t updated_at, created_at, deployed_at, serving_verified_at;
    char error[PC_ERROR], serving_error[PC_ERROR];
} pc_deploy;

typedef struct {
    int state, status_code, latency_ms;
    int history[PC_HISTORY]; /* -1 = unknown/missing, never an outage claim. */
    int history_count;
    time_t updated_at;
    char error[PC_ERROR];
} pc_health;

typedef struct {
    uint64_t samples, failures, unknown;
    double availability_pct; /* -1 until the first observed success/failure. */
    time_t window_start, updated_at;
    char endpoint[512], predicate[256];
} pc_uptime;

typedef struct {
    pc_pr_list prs;
    pc_garden garden;
    pc_deploy deploy;
    pc_health health;
    pc_uptime uptime;
} pc_snapshot;
typedef pc_snapshot pc_state;

typedef struct {
    char github_login[128], github_org[128], github_token_file[512];
    char vercel_scope[128], vercel_project[128], vercel_token_file[512];
    char production_alias[256], health_url[512], health_expected[256];
    char https_helper[512], ca_file[512];
    char text[512], text_label[32], countdown_label[32];
    time_t countdown_target;
    int world_offset_minutes;
    char world_label[32], brand_frame[512], identity_frame[512];
    char identity_label[64], timezone[128];
    uint32_t foreground, accent, secondary;
} pc_provider_config;
typedef pc_provider_config pc_config;

/* Call on the provider thread with its private snapshot copy. No device
 * credentials are imported from desktop CLI stores by any native provider. */
int pc_provider_config_load(const char *path, pc_provider_config *config, char *error, size_t error_size);
void pc_collect(const pc_provider_config *config, pc_snapshot *snapshot);
void pc_collect_cloud(const pc_provider_config *config, pc_snapshot *snapshot);
void pc_probe_health(const pc_provider_config *config, pc_snapshot *snapshot);

/* Parser entrypoints are public to permit fixture-based offline tests. They
 * reject GraphQL errors and preserve failure/unknown states explicitly. */
int pc_parse_pr_page(const char *body, pc_pr_list *list, char *next_cursor, size_t cursor_size);
int pc_parse_garden(const char *body, pc_garden *garden);
int pc_parse_deployments(const char *body, pc_deploy *deploy, char *deployment_id, size_t id_size);
int pc_parse_serving(const char *body, const char *alias, pc_deploy *deploy);
int pc_health_json_matches(const char *body, const char *expected);

#endif
