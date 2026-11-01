/* SPDX-License-Identifier: GPL-3.0-or-later */
#include "json.h"
#include <assert.h>
int main(void) {
    pc_json doc;
    const char *invalid[] = {"{\"status\":\"ok\",junk}", "{\"status\" \"ok\"}", "{\"x\":1,}", "[1,]", "NaN", "01", "1e", "{}{}", "{\"x\":\"\\q\"}"};
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++) assert(pc_json_parse(&doc, invalid[i]) < 0);
    assert(pc_json_parse(&doc, "{\"list\":[{\"value\":12},false,null],\"unicode\":\"A\\u00e9\\ud83d\\ude80\",\"status\":\"old\",\"status\":\"new\"}") > 0);
    int array = pc_json_get(&doc, 0, "list");
    assert(pc_json_count(&doc, array) == 3);
    assert(pc_json_integer(&doc, pc_json_get(&doc, pc_json_index(&doc, array, 0), "value"), -1) == 12);
    assert(pc_json_bool(&doc, pc_json_index(&doc, array, 1), 1) == 0);
    assert(pc_json_is_null(&doc, pc_json_index(&doc, array, 2)));
    char value[64]; assert(pc_json_string(&doc, pc_json_get(&doc, 0, "unicode"), value, sizeof(value)) == 0);
    assert(!strcmp(value, "A\xc3\xa9\xf0\x9f\x9a\x80"));
    assert(pc_json_string(&doc, pc_json_get(&doc, 0, "unicode"), value, 2) < 0);
    assert(pc_json_string(&doc, pc_json_get(&doc, 0, "status"), value, sizeof(value)) == 0 && !strcmp(value, "new"));
    pc_json_free(&doc);
    char deep[160]; memset(deep, '[', 70); memset(deep + 70, ']', 70); deep[140] = 0;
    assert(pc_json_parse(&doc, deep) < 0);
    assert(pc_json_escape("a\n\"b", value, sizeof(value)) && !strcmp(value, "\"a\\u000a\\\"b\""));
    puts("Bounded JSON grammar, traversal, duplicate-key and Unicode contracts passed");
    return 0;
}
