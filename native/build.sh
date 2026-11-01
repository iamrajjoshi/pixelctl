#!/bin/sh
set -eu
NATIVE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for pair in 'clock receiver' 'supervisor supervisor' 'res-flash res-flash'; do
    set -- $pair
    "${ZIG:-zig}" cc -target arm-linux-musleabihf -mcpu=cortex_a7 -Os -static -Wall -Wextra "$NATIVE_DIR/$1.c" -o "$NATIVE_DIR/$2-armv7"
done
"${ZIG:-zig}" cc -target arm-linux-gnueabihf.2.30 -mcpu=cortex_a7 -Os -shared -fPIC -Wall -Wextra "$NATIVE_DIR/loader.c" -ldl -o "$NATIVE_DIR/libpixelctl.so"
"${ZIG:-zig}" cc -target arm-linux-musleabihf -mcpu=cortex_a7 -Os -static -Wall -Wextra -pthread "$NATIVE_DIR/standalone.c" "$NATIVE_DIR/standalone_providers.c" "$NATIVE_DIR/standalone_pages.c" -lm -o "$NATIVE_DIR/standalone-armv7"
${CC:-cc} -Os -Wall -Wextra -Werror -pthread "$NATIVE_DIR/standalone.c" "$NATIVE_DIR/standalone_providers.c" "$NATIVE_DIR/standalone_pages.c" -lm -o "$NATIVE_DIR/standalone-host"
${CC:-cc} -Os -Wall -Wextra "$NATIVE_DIR/clock.c" -o "$NATIVE_DIR/receiver-host"
${CC:-cc} -Os -Wall -Wextra "$NATIVE_DIR/res-flash.c" -o "$NATIVE_DIR/res-flash-host"
python3 "$NATIVE_DIR/test_receiver.py" "$NATIVE_DIR/receiver-host"
python3 "$NATIVE_DIR/test_flash.py" "$NATIVE_DIR/res-flash-host"
${CC:-cc} -Os -Wall -Wextra -Werror "$NATIVE_DIR/test_json.c" -o "$NATIVE_DIR/json-host"
"$NATIVE_DIR/json-host"
${CC:-cc} -Os -Wall -Wextra -Werror -fsanitize=undefined "$NATIVE_DIR/test_pages.c" "$NATIVE_DIR/standalone_pages.c" -lm -o "$NATIVE_DIR/pages-host"
"$NATIVE_DIR/pages-host"
python3 "$NATIVE_DIR/test_engine.py" "$NATIVE_DIR/standalone-host" "$NATIVE_DIR/receiver-host"
python3 "$NATIVE_DIR/test_providers_native.py"
if [ -n "${MBEDTLS_WORK:-}" ]; then
    python3 "$NATIVE_DIR/build_tls.py" --zig "${ZIG:-zig}" --work "$MBEDTLS_WORK"
fi
