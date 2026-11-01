/* SPDX-License-Identifier: GPL-3.0-or-later
 * Bounded JSON helpers over jsmn (MIT). Strings are decoded into caller buffers;
 * input is borrowed and token storage is private to each document.
 */
#ifndef PIXELCTL_JSON_H
#define PIXELCTL_JSON_H
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#define JSMN_STATIC
#define JSMN_STRICT
#include "jsmn.h"
typedef struct { const char *text; jsmntok_t *tokens; int count; } pc_json;
static inline void pc_json_space(const char **p) { while (**p == ' ' || **p == '\t' || **p == '\r' || **p == '\n') (*p)++; }
static inline int pc_json_validate_string(const char **p) {
    if (*(*p)++ != '"') return 0;
    while (**p && **p != '"') {
        unsigned char c = (unsigned char)*(*p)++;
        if (c < 32) return 0;
        if (c == '\\') {
            char escape = *(*p)++;
            if (!escape) return 0;
            if (escape == 'u') {
                for (int i = 0; i < 4; i++) {
                    unsigned char h = (unsigned char)**p;
                    if (!((h >= '0' && h <= '9') || (h >= 'a' && h <= 'f') || (h >= 'A' && h <= 'F'))) return 0;
                    (*p)++;
                }
            } else if (!strchr("\"\\/bfnrt", escape)) return 0;
        }
    }
    if (**p != '"') return 0;
    (*p)++; return 1;
}
static inline int pc_json_validate_value(const char **p, unsigned depth) {
    if (depth > 64) return 0;
    pc_json_space(p);
    if (**p == '"') return pc_json_validate_string(p);
    if (**p == '{' || **p == '[') {
        int object = **p == '{'; char closing = object ? '}' : ']'; (*p)++;
        pc_json_space(p);
        if (**p == closing) { (*p)++; return 1; }
        for (;;) {
            if (object) {
                if (**p != '"' || !pc_json_validate_string(p)) return 0;
                pc_json_space(p); if (**p != ':') return 0; (*p)++;
            }
            if (!pc_json_validate_value(p, depth + 1)) return 0;
            pc_json_space(p);
            if (**p == closing) { (*p)++; return 1; }
            if (**p != ',') return 0;
            (*p)++; pc_json_space(p);
        }
    }
    if (!strncmp(*p, "true", 4)) { *p += 4; return 1; }
    if (!strncmp(*p, "false", 5)) { *p += 5; return 1; }
    if (!strncmp(*p, "null", 4)) { *p += 4; return 1; }
    if (**p == '-') (*p)++;
    if (**p == '0') (*p)++;
    else if (**p >= '1' && **p <= '9') { do { (*p)++; } while (**p >= '0' && **p <= '9'); }
    else return 0;
    if (**p == '.') {
        (*p)++; if (**p < '0' || **p > '9') return 0;
        do { (*p)++; } while (**p >= '0' && **p <= '9');
    }
    if (**p == 'e' || **p == 'E') {
        (*p)++; if (**p == '+' || **p == '-') (*p)++;
        if (**p < '0' || **p > '9') return 0;
        do { (*p)++; } while (**p >= '0' && **p <= '9');
    }
    return 1;
}
static inline int pc_json_skip(const pc_json *doc, int token) {
    if (token < 0 || token >= doc->count) return doc->count;
    int next = token + 1;
    while (next < doc->count && doc->tokens[next].start < doc->tokens[token].end) next++;
    return next;
}
static inline int pc_json_parse(pc_json *doc, const char *text) {
    memset(doc, 0, sizeof(*doc));
    if (!text || strlen(text) > 1024 * 1024) return -1;
    const char *end = text;
    if (!pc_json_validate_value(&end, 0)) return -1;
    pc_json_space(&end); if (*end) return -1;
    doc->text = text;
    for (unsigned capacity = 256; capacity <= 32768; capacity *= 2) {
        jsmntok_t *tokens = realloc(doc->tokens, capacity * sizeof(*tokens));
        if (!tokens) break;
        doc->tokens = tokens;
        jsmn_parser parser; jsmn_init(&parser);
        int result = jsmn_parse(&parser, text, strlen(text), tokens, capacity);
        if (result == JSMN_ERROR_NOMEM) continue;
        if (result > 0) {
            doc->count = result;
            if (pc_json_skip(doc, 0) != result) break;
            return result;
        }
        break;
    }
    free(doc->tokens); doc->tokens = NULL; doc->count = 0; return -1;
}
static inline void pc_json_free(pc_json *doc) { free(doc->tokens); memset(doc, 0, sizeof(*doc)); }
static inline int pc_json_equal(const pc_json *doc, int token, const char *value) {
    if (token < 0 || token >= doc->count) return 0;
    const jsmntok_t *t = &doc->tokens[token];
    return (size_t)(t->end - t->start) == strlen(value) && !memcmp(doc->text + t->start, value, strlen(value));
}
static inline int pc_json_find(const pc_json *doc, int object, const char *key) {
    if (object < 0 || object >= doc->count || doc->tokens[object].type != JSMN_OBJECT) return -1;
    int found = -1;
    for (int i = object + 1; i < doc->count && doc->tokens[i].start < doc->tokens[object].end; ) {
        if (doc->tokens[i].type != JSMN_STRING || i + 1 >= doc->count) return -1;
        int value = i + 1;
        if (pc_json_equal(doc, i, key)) found = value;
        i = pc_json_skip(doc, value);
    }
    return found;
}
static inline int pc_json_get(const pc_json *doc, int object, const char *key) { return pc_json_find(doc, object, key); }
static inline int pc_json_array(const pc_json *doc, int array, int index) {
    if (array < 0 || array >= doc->count || index < 0 || doc->tokens[array].type != JSMN_ARRAY) return -1;
    int i = array + 1;
    while (index-- && i < doc->count) i = pc_json_skip(doc, i);
    return i < doc->count && doc->tokens[i].start < doc->tokens[array].end ? i : -1;
}
static inline int pc_json_index(const pc_json *doc, int array, int index) { return pc_json_array(doc, array, index); }
static inline int pc_json_count(const pc_json *doc, int array) { return array >= 0 && array < doc->count ? doc->tokens[array].size : 0; }
static inline int pc_json_hex4(const char *p) {
    int value = 0;
    for (int i = 0; i < 4; i++) {
        int digit = p[i] >= '0' && p[i] <= '9' ? p[i] - '0' : p[i] >= 'a' && p[i] <= 'f' ? p[i] - 'a' + 10 : p[i] >= 'A' && p[i] <= 'F' ? p[i] - 'A' + 10 : -1;
        if (digit < 0) return -1;
        value = value * 16 + digit;
    }
    return value;
}
static inline int pc_json_string(const pc_json *doc, int token, char *out, size_t capacity) {
    if (!capacity) return -1;
    out[0] = 0;
    if (token < 0 || token >= doc->count || doc->tokens[token].type != JSMN_STRING) return -1;
    const char *p = doc->text + doc->tokens[token].start, *end = doc->text + doc->tokens[token].end;
    size_t have = 0;
    while (p < end) {
        unsigned char bytes[4]; size_t count = 1;
        bytes[0] = (unsigned char)*p++;
        if (bytes[0] == '\\') {
            if (p >= end) return -1;
            char escape = *p++;
            if (escape == 'u') {
                if (end - p < 4) return -1;
                int code = pc_json_hex4(p); p += 4;
                if (code < 1) return -1;
                if (code >= 0xd800 && code <= 0xdbff) {
                    if (end - p < 6 || p[0] != '\\' || p[1] != 'u') return -1;
                    int low = pc_json_hex4(p + 2); p += 6;
                    if (low < 0xdc00 || low > 0xdfff) return -1;
                    code = 0x10000 + ((code - 0xd800) << 10) + low - 0xdc00;
                } else if (code >= 0xdc00 && code <= 0xdfff) return -1;
                if (code < 128) bytes[0] = (unsigned char)code;
                else if (code < 2048) { bytes[0] = 0xc0 | (code >> 6); bytes[1] = 0x80 | (code & 63); count = 2; }
                else if (code < 65536) { bytes[0] = 0xe0 | (code >> 12); bytes[1] = 0x80 | ((code >> 6) & 63); bytes[2] = 0x80 | (code & 63); count = 3; }
                else { bytes[0] = 0xf0 | (code >> 18); bytes[1] = 0x80 | ((code >> 12) & 63); bytes[2] = 0x80 | ((code >> 6) & 63); bytes[3] = 0x80 | (code & 63); count = 4; }
            } else {
                switch (escape) {
                    case '"': bytes[0] = '"'; break; case '\\': bytes[0] = '\\'; break; case '/': bytes[0] = '/'; break;
                    case 'b': bytes[0] = '\b'; break; case 'f': bytes[0] = '\f'; break; case 'n': bytes[0] = '\n'; break;
                    case 'r': bytes[0] = '\r'; break; case 't': bytes[0] = '\t'; break; default: return -1;
                }
            }
        } else if (bytes[0] < 32) return -1;
        if (have + count >= capacity) { out[have] = 0; return -1; }
        memcpy(out + have, bytes, count); have += count;
    }
    out[have] = 0; return 0;
}
static inline int pc_json_raw(const pc_json *doc, int token, char *out, size_t capacity) {
    if (!capacity || token < 0 || token >= doc->count) return -1;
    int start = doc->tokens[token].start, end = doc->tokens[token].end;
    if (doc->tokens[token].type == JSMN_STRING) { start--; end++; }
    if (start < 0 || end < start || (size_t)(end - start) >= capacity) { out[0] = 0; return -1; }
    memcpy(out, doc->text + start, (size_t)(end - start)); out[end - start] = 0; return 0;
}
static inline double pc_json_number(const pc_json *doc, int token, double fallback) {
    char value[96];
    if (token < 0 || token >= doc->count || doc->tokens[token].type != JSMN_PRIMITIVE || pc_json_raw(doc, token, value, sizeof(value))) return fallback;
    char *end; errno = 0; double result = strtod(value, &end);
    return errno || end == value || *end ? fallback : result;
}
static inline long long pc_json_integer(const pc_json *doc, int token, long long fallback) {
    char value[96];
    if (token < 0 || token >= doc->count || doc->tokens[token].type != JSMN_PRIMITIVE || pc_json_raw(doc, token, value, sizeof(value))) return fallback;
    char *end; errno = 0; long long result = strtoll(value, &end, 10);
    return errno || end == value || *end ? fallback : result;
}
static inline int pc_json_boolean(const pc_json *doc, int token, int fallback) {
    if (token < 0 || token >= doc->count || doc->tokens[token].type != JSMN_PRIMITIVE) return fallback;
    return pc_json_equal(doc, token, "true") ? 1 : pc_json_equal(doc, token, "false") ? 0 : fallback;
}
static inline int pc_json_bool(const pc_json *doc, int token, int fallback) { return pc_json_boolean(doc, token, fallback); }
static inline int pc_json_is_null(const pc_json *doc, int token) { return token >= 0 && token < doc->count && doc->tokens[token].type == JSMN_PRIMITIVE && pc_json_equal(doc, token, "null"); }
static inline size_t pc_json_escape(const char *value, char *out, size_t capacity) {
    if (capacity < 3) return 0;
    size_t have = 0; out[have++] = '"';
    for (const unsigned char *p = (const unsigned char *)value; *p; p++) {
        char escaped[7]; const char *bytes = (const char *)p; size_t count = 1;
        if (*p == '"' || *p == '\\') { escaped[0] = '\\'; escaped[1] = *p; bytes = escaped; count = 2; }
        else if (*p < 32) { snprintf(escaped, sizeof(escaped), "\\u%04x", *p); bytes = escaped; count = 6; }
        if (have + count + 2 > capacity) { out[0] = 0; return 0; }
        memcpy(out + have, bytes, count); have += count;
    }
    out[have++] = '"'; out[have] = 0; return have;
}
#endif
