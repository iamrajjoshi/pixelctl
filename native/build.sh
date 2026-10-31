#!/bin/sh
set -eu
NATIVE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for pair in 'clock receiver' 'supervisor supervisor' 'res-flash res-flash'; do
    set -- $pair
    "${ZIG:-zig}" cc -target arm-linux-musleabihf -mcpu=cortex_a7 -Os -static -Wall -Wextra "$NATIVE_DIR/$1.c" -o "$NATIVE_DIR/$2-armv7"
done
"${ZIG:-zig}" cc -target arm-linux-gnueabihf.2.30 -mcpu=cortex_a7 -Os -shared -fPIC -Wall -Wextra "$NATIVE_DIR/loader.c" -ldl -o "$NATIVE_DIR/libpixelctl.so"
${CC:-cc} -Os -Wall -Wextra "$NATIVE_DIR/clock.c" -o "$NATIVE_DIR/receiver-host"
${CC:-cc} -Os -Wall -Wextra "$NATIVE_DIR/res-flash.c" -o "$NATIVE_DIR/res-flash-host"
python3 "$NATIVE_DIR/test_receiver.py" "$NATIVE_DIR/receiver-host"
python3 "$NATIVE_DIR/test_flash.py" "$NATIVE_DIR/res-flash-host"
if [ -n "${MBEDTLS_WORK:-}" ]; then
    python3 "$NATIVE_DIR/build_tls.py" --zig "${ZIG:-zig}" --work "$MBEDTLS_WORK"
fi
