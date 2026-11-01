/* SPDX-License-Identifier: GPL-3.0-or-later
 * Optional native data sources. Credentials are supplied as explicit private
 * device files; this code never imports desktop account credentials.
 */
#define _DEFAULT_SOURCE
#define _DARWIN_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "standalone_providers.h"
#include "json.h"
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define HTTP_LIMIT (2U * 1024U * 1024U)
#define BODY_LIMIT (1024U * 1024U)
typedef struct { int status, latency_ms; char *body; } pc_http;
/* Keep pipe creation, close-on-exec setup and fork indivisible relative to
 * other provider workers. Network reads remain fully concurrent. */
static pthread_mutex_t spawn_mutex = PTHREAD_MUTEX_INITIALIZER;

static void copy_string(char *out, size_t size, const char *value) {
    if (size) snprintf(out, size, "%s", value ? value : "");
}
static int get_string(const pc_json *doc, int object, const char *key, char *out, size_t size) {
    int index = pc_json_get(doc, object, key);
    if (index < 0 || pc_json_string(doc, index, out, size) < 0) { if (size) out[0] = 0; return -1; }
    return 0;
}
static int string_equals(const pc_json *doc, int index, const char *value) {
    char text[512];
    return index >= 0 && pc_json_string(doc, index, text, sizeof(text)) == 0 && !strcmp(text, value);
}
static int64_t monotonic_ms(void) {
    struct timespec value; clock_gettime(CLOCK_MONOTONIC, &value);
    return (int64_t)value.tv_sec * 1000 + value.tv_nsec / 1000000;
}
static time_t iso_time(const char *text) {
    struct tm value = {0};
    int year, month;
    if (sscanf(text, "%d-%d-%dT%d:%d:%d", &year, &month, &value.tm_mday, &value.tm_hour, &value.tm_min, &value.tm_sec) != 6) return 0;
    value.tm_year = year - 1900; value.tm_mon = month - 1;
    return timegm(&value);
}
static char *read_file(const char *path, size_t limit) {
    FILE *file = fopen(path, "rb");
    if (!file) return NULL;
    if (fseek(file, 0, SEEK_END) || ftell(file) < 0) { fclose(file); return NULL; }
    long size = ftell(file);
    if ((unsigned long)size > limit || fseek(file, 0, SEEK_SET)) { fclose(file); return NULL; }
    char *text = calloc((size_t)size + 1, 1);
    if (!text || fread(text, 1, (size_t)size, file) != (size_t)size) { free(text); fclose(file); return NULL; }
    fclose(file); return text;
}
static uint32_t parse_color(const char *text, uint32_t fallback) {
    if (!text || text[0] != '#' || strlen(text) != 7) return fallback;
    char *end; unsigned long value = strtoul(text + 1, &end, 16);
    return *end ? fallback : (uint32_t)value;
}
int pc_provider_config_load(const char *path, pc_provider_config *config, char *error, size_t error_size) {
    memset(config, 0, sizeof(*config));
    copy_string(config->https_helper, sizeof(config->https_helper), "/data/pixelctl/https-get");
    copy_string(config->ca_file, sizeof(config->ca_file), "/data/pixelctl/cacert.pem");
    copy_string(config->health_expected, sizeof(config->health_expected), "{\"status\":\"ok\"}");
    copy_string(config->timezone, sizeof(config->timezone), "UTC");
    config->foreground = 0xe8e7dc; config->accent = 0xb7bdc2; config->secondary = 0x89a1a2;
    char *text = read_file(path, 65536);
    pc_json doc;
    if (!text || pc_json_parse(&doc, text) < 0) { free(text); copy_string(error, error_size, "Cannot read valid provider config"); return -1; }
    if (doc.tokens[0].type != JSMN_OBJECT) { pc_json_free(&doc); free(text); copy_string(error,error_size,"Config must be a JSON object"); return -1; }
    int settings = pc_json_get(&doc, 0, "settings");
#define LOAD(field) do { int token = pc_json_get(&doc, 0, #field); if (token < 0) token = pc_json_get(&doc, settings, #field); if (token >= 0 && pc_json_string(&doc, token, config->field, sizeof(config->field)) < 0) { pc_json_free(&doc); free(text); copy_string(error,error_size,"Config string invalid or too long"); return -1; } } while (0)
    LOAD(github_login); LOAD(github_org); LOAD(github_token_file);
    LOAD(vercel_scope); LOAD(vercel_project); LOAD(vercel_token_file);
    LOAD(production_alias); LOAD(health_url); LOAD(https_helper); LOAD(ca_file);
    LOAD(text); LOAD(text_label); LOAD(countdown_label); LOAD(world_label);
    LOAD(brand_frame); LOAD(identity_frame); LOAD(identity_label); LOAD(timezone);
#undef LOAD
    int expected = pc_json_get(&doc, 0, "health_expected");
    if (expected >= 0 && pc_json_raw(&doc, expected, config->health_expected, sizeof(config->health_expected)) < 0) {
        pc_json_free(&doc); free(text); copy_string(error,error_size,"Health predicate too long"); return -1;
    }
    int countdown = pc_json_get(&doc, 0, "countdown_target");
    if (countdown < 0) countdown = pc_json_get(&doc, settings, "countdown_target");
    char date[64];
    if (countdown >= 0 && pc_json_string(&doc, countdown, date, sizeof(date)) == 0) config->countdown_target = iso_time(date);
    else config->countdown_target = (time_t)pc_json_integer(&doc, countdown, 0);
    int offset = pc_json_get(&doc, 0, "world_offset_minutes");
    if (offset < 0) offset = pc_json_get(&doc, settings, "world_offset_minutes");
    config->world_offset_minutes = (int)pc_json_integer(&doc, offset, 0);
    int theme = pc_json_get(&doc, 0, "theme");
    const char *colors[] = {"foreground", "accent", "secondary"};
    uint32_t *outputs[] = {&config->foreground, &config->accent, &config->secondary};
    for (int i = 0; i < 3; i++) {
        int token = pc_json_get(&doc, 0, colors[i]);
        if (token < 0 && theme >= 0) token = pc_json_get(&doc, theme, colors[i]);
        if (token >= 0 && pc_json_string(&doc, token, date, sizeof(date)) == 0) *outputs[i] = parse_color(date, *outputs[i]);
    }
    pc_json_free(&doc); free(text);
    if (config->health_url[0] && strncmp(config->health_url, "https://", 8)) { copy_string(error,error_size,"Health URL must use HTTPS"); return -1; }
    if (config->health_url[0] && !pc_health_json_matches(config->health_expected, config->health_expected)) { copy_string(error,error_size,"Health predicate must be a nonempty JSON object"); return -1; }
    if (error_size) error[0] = 0;
    return 0;
}

/* Spawn the verified TLS utility without a shell; only token-file paths appear
 * in argv. Bound both child wall time and output size independently of TLS. */
static int http_request(const pc_provider_config *config, const char *url, const char *token_file, const char *post, pc_http *result) {
    memset(result, 0, sizeof(*result)); result->latency_ms = -1;
    int pipefd[2], status = 0, failed = 0;
    char request_file[] = "/tmp/pixelctl-request.XXXXXX";
    if (post) {
        int fd = mkstemp(request_file);
        if (fd < 0) return -1;
        size_t size = strlen(post), offset = 0;
        while (offset < size) { ssize_t count = write(fd, post + offset, size - offset); if (count <= 0) { close(fd); unlink(request_file); return -1; } offset += (size_t)count; }
        close(fd);
    }
    if (pthread_mutex_lock(&spawn_mutex)) { if (post) unlink(request_file); return -1; }
    if (pipe(pipefd)) {
        pthread_mutex_unlock(&spawn_mutex);
        if (post) unlink(request_file);
        return -1;
    }
    if (fcntl(pipefd[0], F_SETFD, FD_CLOEXEC) < 0 || fcntl(pipefd[1], F_SETFD, FD_CLOEXEC) < 0) {
        close(pipefd[0]); close(pipefd[1]);
        pthread_mutex_unlock(&spawn_mutex);
        if (post) unlink(request_file);
        return -1;
    }
    int64_t started = monotonic_ms();
    pid_t child = fork();
    if (child != 0) pthread_mutex_unlock(&spawn_mutex);
    if (child == 0) {
        close(pipefd[0]); dup2(pipefd[1], STDOUT_FILENO); close(pipefd[1]);
        int quiet = open("/dev/null", O_WRONLY); if (quiet >= 0) { dup2(quiet, STDERR_FILENO); close(quiet); }
        char *args[14]; int n = 0;
        args[n++] = (char *)config->https_helper; args[n++] = (char *)url;
        args[n++] = "--ca"; args[n++] = (char *)config->ca_file;
        if (token_file && token_file[0]) { args[n++] = "--token-file"; args[n++] = (char *)token_file; }
        if (post) { args[n++] = "--method"; args[n++] = "POST"; args[n++] = "--body-file"; args[n++] = request_file; }
        args[n] = NULL; execv(args[0], args); _exit(127);
    }
    close(pipefd[1]);
    if (child < 0) { close(pipefd[0]); if (post) unlink(request_file); return -1; }
    char *output = malloc(HTTP_LIMIT + 1); size_t used = 0;
    if (!output) failed = 1;
    while (!failed) {
        int remaining = (int)(45000 - (monotonic_ms() - started));
        if (remaining <= 0 || used == HTTP_LIMIT) { failed = 1; break; }
        struct pollfd pending = {.fd = pipefd[0], .events = POLLIN};
        int ready = poll(&pending, 1, remaining);
        if (ready < 0 && errno == EINTR) continue;
        if (ready <= 0) { failed = 1; break; }
        ssize_t count = read(pipefd[0], output + used, HTTP_LIMIT - used);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) { failed = 1; break; }
        if (count == 0) break;
        used += (size_t)count;
    }
    close(pipefd[0]);
    if (failed) kill(child, SIGKILL);
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
    if (post) unlink(request_file);
    if (failed || !WIFEXITED(status) || (WEXITSTATUS(status) != 0 && WEXITSTATUS(status) != 22)) { free(output); return -1; }
    output[used] = 0;
    pc_json envelope;
    if (pc_json_parse(&envelope, output) < 0) { free(output); return -1; }
    int verified = pc_json_boolean(&envelope, pc_json_get(&envelope, 0, "tls_verified"), 0)
        && pc_json_boolean(&envelope, pc_json_get(&envelope, 0, "hostname_verified"), 0);
    result->status = (int)pc_json_integer(&envelope, pc_json_get(&envelope, 0, "status"), 0);
    result->body = malloc(BODY_LIMIT + 1);
    if (!verified || !result->body || pc_json_string(&envelope, pc_json_get(&envelope, 0, "body"), result->body, BODY_LIMIT + 1) < 0) {
        free(result->body); result->body = NULL; pc_json_free(&envelope); free(output); return -1;
    }
    result->latency_ms = (int)(monotonic_ms() - started);
    pc_json_free(&envelope); free(output); return 0;
}
static int api_request(const pc_provider_config *config, const char *url, const char *token, const char *post, pc_http *result) {
    if (http_request(config, url, token, post, result) < 0) return -1;
    if (result->status < 200 || result->status >= 300) { free(result->body); result->body = NULL; return -1; }
    return 0;
}
static int name_is_safe(const char *value) {
    if (!value[0]) return 0;
    for (const unsigned char *p = (const unsigned char *)value; *p; p++) if (!isalnum(*p) && *p != '-' && *p != '_' && *p != '.') return 0;
    return 1;
}
static int status_from_string(const char *text) {
    if (!strcmp(text,"SUCCESS") || !strcmp(text,"READY")) return PC_OK;
    if (!strcmp(text,"PENDING") || !strcmp(text,"EXPECTED") || !strcmp(text,"BUILDING") || !strcmp(text,"QUEUED") || !strcmp(text,"INITIALIZING")) return PC_BUILDING;
    if (!strcmp(text,"FAILURE") || !strcmp(text,"ERROR")) return PC_FAILED;
    if (!strcmp(text,"CANCELED") || !strcmp(text,"CANCELLED")) return PC_CANCELLED;
    return PC_UNKNOWN;
}
static int json_open_api(pc_json *doc, const char *body) {
    if (pc_json_parse(doc, body) < 0) return -1;
    int errors = pc_json_get(doc, 0, "errors");
    if (errors >= 0 && !pc_json_is_null(doc, errors) && pc_json_count(doc, errors) > 0) { pc_json_free(doc); return -1; }
    return 0;
}
int pc_parse_pr_page(const char *body, pc_pr_list *list, char *next_cursor, size_t cursor_size) {
    pc_json doc;
    if (json_open_api(&doc, body) < 0) return -1;
    int data = pc_json_get(&doc, 0, "data"), search = pc_json_get(&doc, data, "search");
    int nodes = pc_json_get(&doc, search, "nodes"), page = pc_json_get(&doc, search, "pageInfo");
    if (search < 0 || nodes < 0 || page < 0) { pc_json_free(&doc); return -1; }
    list->total = (int)pc_json_integer(&doc, pc_json_get(&doc, search, "issueCount"), 0);
    for (int i = 0, node; (node = pc_json_index(&doc, nodes, i)) >= 0; i++) {
        if (pc_json_is_null(&doc, node)) continue;
        if (list->count >= PC_MAX_PRS) break;
        pc_pr *pr = &list->items[list->count]; memset(pr, 0, sizeof(*pr));
        pr->number = (int)pc_json_integer(&doc, pc_json_get(&doc, node, "number"), 0);
        int title = pc_json_get(&doc,node,"title");
        if (title >= 0) pc_json_string(&doc,title,pr->title,sizeof(pr->title));
        if (pr->number <= 0 || !pr->title[0]) { pc_json_free(&doc); return -1; }
        int repo = pc_json_get(&doc,node,"repository");
        get_string(&doc,repo,"nameWithOwner",pr->repo,sizeof(pr->repo));
        pr->draft = pc_json_boolean(&doc, pc_json_get(&doc,node,"isDraft"),0);
        char value[128];
        get_string(&doc,node,"reviewDecision",value,sizeof(value));
        if (!strcmp(value,"APPROVED")) pr->review = PC_REVIEW_APPROVED;
        else if (!strcmp(value,"CHANGES_REQUESTED")) pr->review = PC_REVIEW_CHANGES;
        else if (!strcmp(value,"REVIEW_REQUIRED")) pr->review = PC_REVIEW_REQUIRED;
        get_string(&doc,node,"updatedAt",value,sizeof(value)); pr->updated_at = iso_time(value);
        int commits = pc_json_get(&doc,node,"commits"), commit_nodes = pc_json_get(&doc,commits,"nodes");
        int commit_node = pc_json_index(&doc,commit_nodes,0), commit = pc_json_get(&doc,commit_node,"commit");
        int rollup = pc_json_get(&doc,commit,"statusCheckRollup");
        get_string(&doc,rollup,"state",value,sizeof(value)); pr->status = status_from_string(value);
        list->count++;
    }
    int more = pc_json_boolean(&doc,pc_json_get(&doc,page,"hasNextPage"),0);
    if (cursor_size) next_cursor[0] = 0;
    if (more && get_string(&doc,page,"endCursor",next_cursor,cursor_size) < 0) { pc_json_free(&doc); return -1; }
    pc_json_free(&doc); return more;
}
int pc_parse_garden(const char *body, pc_garden *garden) {
    pc_json doc;
    if (json_open_api(&doc,body) < 0) return -1;
    int node = pc_json_get(&doc,0,"data"); node = pc_json_get(&doc,node,"user");
    node = pc_json_get(&doc,node,"contributionsCollection"); node = pc_json_get(&doc,node,"contributionCalendar");
    int weeks = pc_json_get(&doc,node,"weeks");
    if (weeks < 0) { pc_json_free(&doc); return -1; }
    pc_garden next = {0};
    for (int i=0,week;(week=pc_json_index(&doc,weeks,i))>=0;i++) {
        int days=pc_json_get(&doc,week,"contributionDays");
        for (int j=0,day;(day=pc_json_index(&doc,days,j))>=0;j++) {
            if (next.count >= PC_GARDEN_DAYS) { pc_json_free(&doc); return -1; }
            pc_contribution *value=&next.days[next.count];
            if (get_string(&doc,day,"date",value->date,sizeof(value->date))<0 || strlen(value->date)!=10) { pc_json_free(&doc); return -1; }
            value->count=(int)pc_json_integer(&doc,pc_json_get(&doc,day,"contributionCount"),0);
            next.total+=value->count;next.count++;
        }
    }
    pc_json_free(&doc);next.updated_at=time(NULL);*garden=next;return 0;
}
int pc_parse_deployments(const char *body, pc_deploy *deploy, char *deployment_id, size_t id_size) {
    pc_json doc;
    if (json_open_api(&doc,body)<0)return -1;
    int array=pc_json_get(&doc,0,"deployments"), selected=-1;double created=-1;
    for(int i=0,node;(node=pc_json_index(&doc,array,i))>=0;i++) {
        if(!string_equals(&doc,pc_json_get(&doc,node,"target"),"production"))continue;
        double timestamp=pc_json_number(&doc,pc_json_get(&doc,node,"createdAt"),0);
        if(timestamp==0)timestamp=pc_json_number(&doc,pc_json_get(&doc,node,"created"),0);
        if(timestamp>created){created=timestamp;selected=node;}
    }
    if(selected<0){pc_json_free(&doc);return -1;}
    pc_deploy next={0};char value[512];
    get_string(&doc,selected,"state",value,sizeof(value));next.state=status_from_string(value);
    if(next.state==PC_UNKNOWN){get_string(&doc,selected,"readyState",value,sizeof(value));next.state=status_from_string(value);}
    int meta=pc_json_get(&doc,selected,"meta");get_string(&doc,meta,"githubCommitSha",next.sha,sizeof(next.sha));
    if(get_string(&doc,selected,"url",value,sizeof(value))<0 || strlen(value)+8>=sizeof(next.url)){pc_json_free(&doc);return -1;}
    snprintf(next.url,sizeof(next.url),"https://%.*s",(int)sizeof(next.url)-9,value);
    if(get_string(&doc,selected,"uid",deployment_id,id_size)<0)get_string(&doc,selected,"id",deployment_id,id_size);
    next.created_at=(time_t)(created/1000);next.updated_at=time(NULL);
    if(next.state==PC_OK)next.deployed_at=(time_t)(pc_json_number(&doc,pc_json_get(&doc,selected,"ready"),0)/1000);
    pc_json_free(&doc);*deploy=next;return 0;
}
int pc_parse_serving(const char *body, const char *alias, pc_deploy *deploy) {
    pc_json doc;
    if(json_open_api(&doc,body)<0)return -1;
    if(!string_equals(&doc,pc_json_get(&doc,0,"target"),"production")){pc_json_free(&doc);return -1;}
    int aliases=pc_json_get(&doc,0,"alias");if(aliases<0)aliases=pc_json_get(&doc,0,"aliases");
    int found=0;
    for(int i=0,node;(node=pc_json_index(&doc,aliases,i))>=0;i++)if(string_equals(&doc,node,alias))found=1;
    if(!found){pc_json_free(&doc);return -1;}
    char value[512];get_string(&doc,0,"readyState",value,sizeof(value));deploy->serving_state=status_from_string(value);
    int meta=pc_json_get(&doc,0,"meta");get_string(&doc,meta,"githubCommitSha",deploy->serving_sha,sizeof(deploy->serving_sha));
    if(get_string(&doc,0,"url",value,sizeof(value))<0 || strlen(value)+8>=sizeof(deploy->serving_url)){pc_json_free(&doc);return -1;}
    snprintf(deploy->serving_url,sizeof(deploy->serving_url),"https://%.*s",(int)sizeof(deploy->serving_url)-9,value);
    deploy->live=deploy->state==PC_OK && deploy->serving_state==PC_OK && !strcmp(deploy->url,deploy->serving_url);
    deploy->serving_verified_at=time(NULL);deploy->serving_error[0]=0;pc_json_free(&doc);return 0;
}

static int json_subset(const pc_json *actual,int a,const pc_json *expected,int e) {
    if(a<0 || e<0 || actual->tokens[a].type!=expected->tokens[e].type)return 0;
    if(expected->tokens[e].type==JSMN_OBJECT) {
        for(int token=e+1;token<expected->count && expected->tokens[token].start<expected->tokens[e].end;) {
            char key[256];if(pc_json_string(expected,token,key,sizeof(key))<0)return 0;
            int value=token+1;if(!json_subset(actual,pc_json_get(actual,a,key),expected,value))return 0;
            token=pc_json_skip(expected,value);
        }
        return 1;
    }
    if(expected->tokens[e].type==JSMN_ARRAY) {
        int count=pc_json_count(expected,e);if(count!=pc_json_count(actual,a))return 0;
        for(int i=0;i<count;i++)if(!json_subset(actual,pc_json_index(actual,a,i),expected,pc_json_index(expected,e,i)))return 0;
        return 1;
    }
    char left[512],right[512];
    if(expected->tokens[e].type==JSMN_STRING)return pc_json_string(actual,a,left,sizeof(left))==0 && pc_json_string(expected,e,right,sizeof(right))==0 && !strcmp(left,right);
    return pc_json_raw(actual,a,left,sizeof(left))==0 && pc_json_raw(expected,e,right,sizeof(right))==0 && !strcmp(left,right);
}
int pc_health_json_matches(const char *body,const char *expected) {
    pc_json actual,wanted;
    if(pc_json_parse(&actual,body)<0)return 0;
    if(pc_json_parse(&wanted,expected)<0){pc_json_free(&actual);return 0;}
    int result=wanted.tokens[0].type==JSMN_OBJECT && wanted.tokens[0].size>0 && json_subset(&actual,0,&wanted,0);
    pc_json_free(&wanted);pc_json_free(&actual);return result;
}
void pc_probe_health(const pc_provider_config *config,pc_snapshot *snapshot) {
    if(!config->health_url[0])return;
    pc_health *health=&snapshot->health;pc_uptime *uptime=&snapshot->uptime;
    time_t now=time(NULL);
    int same_predicate = !strcmp(uptime->predicate, config->health_expected)
        || (pc_health_json_matches(uptime->predicate, config->health_expected)
            && pc_health_json_matches(config->health_expected, uptime->predicate));
    if(strcmp(uptime->endpoint,config->health_url) || !same_predicate) {
        memset(uptime,0,sizeof(*uptime));memset(health,0,sizeof(*health));uptime->availability_pct=-1;
        uptime->window_start=now;copy_string(uptime->endpoint,sizeof(uptime->endpoint),config->health_url);copy_string(uptime->predicate,sizeof(uptime->predicate),config->health_expected);
    }
    pc_http response;
    health->state=PC_UNKNOWN;health->status_code=0;health->latency_ms=-1;
    if(http_request(config,config->health_url,NULL,NULL,&response)<0)copy_string(health->error,sizeof(health->error),"Endpoint unreachable; service state unknown");
    else {
        health->status_code=response.status;health->latency_ms=response.latency_ms;
        if(response.status==200 && pc_health_json_matches(response.body,config->health_expected)){health->state=PC_OK;health->error[0]=0;}
        else if(response.status>=500 && response.status<=599){health->state=PC_FAILED;copy_string(health->error,sizeof(health->error),"Observed HTTP server error");}
        else copy_string(health->error,sizeof(health->error),"Unexpected health response");
        free(response.body);
    }
    if(health->state==PC_UNKNOWN)uptime->unknown++;else{uptime->samples++;if(health->state==PC_FAILED)uptime->failures++;}
    uptime->availability_pct=uptime->samples?100.0*(double)(uptime->samples-uptime->failures)/(double)uptime->samples:-1;
    health->updated_at=uptime->updated_at=now;
    if(health->history_count==PC_HISTORY){memmove(health->history,health->history+1,(PC_HISTORY-1)*sizeof(int));health->history_count--;}
    health->history[health->history_count++]=health->state==PC_OK?health->latency_ms:-1;
}

static const char *pr_query="query($search:String!,$after:String){search(query:$search,type:ISSUE,first:100,after:$after){issueCount pageInfo{hasNextPage endCursor}nodes{... on PullRequest{number title url isDraft reviewDecision updatedAt repository{nameWithOwner}commits(last:1){nodes{commit{statusCheckRollup{state}}}}}}}}";
static const char *pr_metadata_query="query($search:String!,$after:String){search(query:$search,type:ISSUE,first:100,after:$after){issueCount pageInfo{hasNextPage endCursor}nodes{... on PullRequest{number title url isDraft reviewDecision updatedAt repository{nameWithOwner}}}}}";
static const char *garden_query="query($login:String!){user(login:$login){contributionsCollection{contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}";
static void collect_github(const pc_provider_config *config,pc_snapshot *snapshot) {
    if(!config->github_login[0])return;
    if(!config->github_token_file[0] || !name_is_safe(config->github_login) || (config->github_org[0] && !name_is_safe(config->github_org))) {
        copy_string(snapshot->prs.error,sizeof(snapshot->prs.error),"GitHub read token/config required");copy_string(snapshot->garden.error,sizeof(snapshot->garden.error),"GitHub read token/config required");return;
    }
    pc_pr_list *next=calloc(1,sizeof(*next));
    if(!next)return;
    char query[2048],search[512],search_json[1100],cursor[512]="",cursor_json[1100],body[4608];
    if(config->github_org[0])snprintf(search,sizeof(search),"org:%s is:pr is:open author:%s sort:updated-desc",config->github_org,config->github_login);
    else snprintf(search,sizeof(search),"is:pr is:open author:%s sort:updated-desc",config->github_login);
    pc_json_escape(search,search_json,sizeof(search_json));
    int failed=0,more=1;
    /* Fine-grained PATs may read PR metadata without access to Checks. Retry
     * that narrower query, retaining an explicit unknown CI state. */
    for(int metadata_only=0;metadata_only<2;metadata_only++) {
        memset(next,0,sizeof(*next));next->checks_available=!metadata_only;cursor[0]=0;failed=0;more=1;
        pc_json_escape(metadata_only?pr_metadata_query:pr_query,query,sizeof(query));
        int response_error=0;
        for(int page=0;page<10 && more;page++) {
            if(cursor[0])pc_json_escape(cursor,cursor_json,sizeof(cursor_json));else strcpy(cursor_json,"null");
            snprintf(body,sizeof(body),"{\"query\":%s,\"variables\":{\"search\":%s,\"after\":%s}}",query,search_json,cursor_json);
            pc_http response;
            if(api_request(config,"https://api.github.com/graphql",config->github_token_file,body,&response)<0){failed=1;break;}
            more=pc_parse_pr_page(response.body,next,cursor,sizeof(cursor));free(response.body);
            if(more<0){failed=1;response_error=1;break;}
        }
        if(!response_error || metadata_only)break;
    }
    if(failed)copy_string(snapshot->prs.error,sizeof(snapshot->prs.error),"GitHub PR query unavailable; check read permissions");
    else {next->complete=!more && next->count==next->total;next->updated_at=time(NULL);if(!next->complete)copy_string(next->error,sizeof(next->error),"GitHub search capped; incomplete PR list");snapshot->prs=*next;}
    free(next);
    pc_json_escape(garden_query,query,sizeof(query));pc_json_escape(config->github_login,search_json,sizeof(search_json));
    snprintf(body,sizeof(body),"{\"query\":%s,\"variables\":{\"login\":%s}}",query,search_json);
    pc_http response;
    if(api_request(config,"https://api.github.com/graphql",config->github_token_file,body,&response)<0)copy_string(snapshot->garden.error,sizeof(snapshot->garden.error),"GitHub contribution query unavailable");
    else {if(pc_parse_garden(response.body,&snapshot->garden)<0)copy_string(snapshot->garden.error,sizeof(snapshot->garden.error),"GitHub contribution response invalid");free(response.body);}
}
static void collect_vercel(const pc_provider_config *config,pc_snapshot *snapshot) {
    if(!config->vercel_project[0] && !config->vercel_scope[0])return;
    pc_deploy *deploy=&snapshot->deploy;
    if(!name_is_safe(config->vercel_scope) || !name_is_safe(config->vercel_project) || !config->vercel_token_file[0]){copy_string(deploy->error,sizeof(deploy->error),"Vercel read token/project/team required");return;}
    char url[2048],project[128],deployment_id[128];pc_http response;
    const char *scope_key=!strncmp(config->vercel_scope,"team_",5)?"teamId":"slug";
    snprintf(url,sizeof(url),"https://api.vercel.com/v9/projects/%s?%s=%s",config->vercel_project,scope_key,config->vercel_scope);
    if(api_request(config,url,config->vercel_token_file,NULL,&response)<0){copy_string(deploy->error,sizeof(deploy->error),"Vercel project query unavailable");return;}
    pc_json doc;int valid=pc_json_parse(&doc,response.body)>=0;
    if(valid){valid=get_string(&doc,0,"id",project,sizeof(project))==0 && name_is_safe(project);pc_json_free(&doc);}free(response.body);
    if(!valid){copy_string(deploy->error,sizeof(deploy->error),"Vercel project response invalid");return;}
    snprintf(url,sizeof(url),"https://api.vercel.com/v6/deployments?projectId=%s&target=production&limit=20&%s=%s",project,scope_key,config->vercel_scope);
    if(api_request(config,url,config->vercel_token_file,NULL,&response)<0){copy_string(deploy->error,sizeof(deploy->error),"Vercel deployments unavailable");return;}
    valid=pc_parse_deployments(response.body,deploy,deployment_id,sizeof(deployment_id))==0;free(response.body);
    if(!valid){copy_string(deploy->error,sizeof(deploy->error),"Vercel production response invalid");return;}
    if(!name_is_safe(config->production_alias)){copy_string(deploy->serving_error,sizeof(deploy->serving_error),"Production alias not configured");return;}
    snprintf(url,sizeof(url),"https://api.vercel.com/v13/deployments/%s?%s=%s&withGitRepoInfo=true",config->production_alias,scope_key,config->vercel_scope);
    if(api_request(config,url,config->vercel_token_file,NULL,&response)<0){copy_string(deploy->serving_error,sizeof(deploy->serving_error),"Production alias verification unavailable");return;}
    if(pc_parse_serving(response.body,config->production_alias,deploy)<0) {
        copy_string(deploy->serving_error,sizeof(deploy->serving_error),"Production alias response invalid");
    }
    free(response.body);
}
void pc_collect_cloud(const pc_provider_config *config,pc_snapshot *snapshot) {
    collect_github(config,snapshot);collect_vercel(config,snapshot);
}
void pc_collect(const pc_provider_config *config,pc_snapshot *snapshot) {
    pc_collect_cloud(config,snapshot);pc_probe_health(config,snapshot);
}
