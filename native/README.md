# TC002 native runtime

This directory contains the device-specific part of pixelctl. It supports the
Ulanzi TC002 / I008 (52×16 RGB, ARMv7 Linux). Page IDs, ordering, timezone,
brightness and clock colors are configuration; company integrations and brand
assets belong in plugins.

## What runs where

`receiver-armv7` owns the LED matrix, caches up to 32 raw RGB pages in RAM, rotates
them, and handles the physical controls. The clock keeps ticking without a Mac.
Other cached data gains an amber bottom row after its configured freshness limit.
Pages marked static retain their artwork without a freshness warning.

`libpixelctl.so` forwards the original vendor application's three EasyUI entry
points. It lets the vendor initialize Wi-Fi, time and MCU communication, then
starts `supervisor-armv7`. After 15 seconds the supervisor suspends the vendor
GUI and runs the receiver. If the receiver exits, the supervisor resumes the
vendor GUI. The kernel, rootfs, radio firmware, MCU and recovery image are retained.
Boot persistence changes only the `res` filesystem: one loader library is added
and `etc/EasyUI.cfg` points to it. The original vendor library stays intact.

The Mac runtime currently supplies rendered integration pages. `https-get-armv7`
also provides verified, independent on-device HTTPS; it is a building block for
future on-device provider plugins, not a claim that every integration runs locally.

## Controls

| Control | Action |
| --- | --- |
| Rotate knob clockwise / counterclockwise | Next / previous page |
| Press knob | Pause / resume automatic rotation |
| Left / right top button | Previous / next page |
| Middle top button | Clock page (or first page if no clock is configured) |

## Receiver API and configuration

Port 9000 requires `X-Pixelctl-Key` for **every** request. The key is read from a
private device file; it is never compiled into a binary. HTTP headers are limited
to 8 KiB and the complete request has a one-second receive deadline.

| Method | Path | Body / response |
| --- | --- | --- |
| GET | `/status` | Current page, order, pause, brightness, device time, counters and update times |
| POST | `/frame/ID` | Exactly 2,496 bytes: 52×16 RGB, row-major |
| GET | `/frame/ID` | Last supplied, unmodified RGB frame |
| GET | `/display` | Actual composed RGB frame, including fallback clock and brightness |
| POST | `/page/ID` | Select a page |
| POST | `/pause` | Toggle automatic rotation |
| POST | `/persist` | Explicitly save cached pages to flash |

Live updates stay in RAM. Do not call `/persist` per frame; occasional snapshots
are sufficient. On reboot, cached data starts stale until refreshed. The fallback
clock uses the device's system time and the configured POSIX timezone.

`--pages-file FILE` accepts one unique ASCII ID per line, up to 32 IDs, each
1–32 letters, digits, `_` or `-`. Default: only `clock`.

Other flags: `--cache DIR`, `--token-file FILE`, `--rotate SECONDS` (8),
`--brightness PERCENT` (40), `--timezone POSIX_TZ` (UTC), `--clock-page ID`
(`clock`), `--clock-mark-width PIXELS` (0), `--clock-color RRGGBB`,
`--date-color RRGGBB`, `--accent-color RRGGBB`, `--stale-after SECONDS` (180),
and repeatable `--static-page ID`. `--simulate` runs the server without hardware.

Installed paths are `/data/pixelctl/{receiver,supervisor,frames,pages,receiver.args}`
and `/data/pixelctl.key`. `receiver.args` contains one CLI argument per line; it
is parsed directly into argv, never executed by a shell.

## Build and tests

The release artifacts were built with Zig 0.17.0 on macOS ARM64. The receiver,
supervisor, NOR helper and HTTPS utility are static ARMv7 musl executables.
Only the 4-KiB EasyUI loader uses the device's glibc 2.30 ABI.

```sh
ZIG=/path/to/zig sh native/build.sh
python3 native/build_tls.py --zig /path/to/zig --work /private/build-cache
```

`build.sh` compiles desktop simulation binaries and runs the native HTTP and
disposable-file NOR contracts. The TLS builder downloads the official Mbed TLS
3.6.7 release, verifies its pinned SHA-256, and builds outside the repository.
Its archive digest is
`a7e8bcbec0e6f761b4af24f25677626b35f762f68eef79c08677a363212d11f6`.
Set `MBEDTLS_WORK` when running `build.sh` to rebuild TLS in the same command.

Hardware SPI is mode 0, 10 MHz, with each 52-pixel row padded to 64 pixels. GPIO35
is low for 1 ms before transmission and high afterwards. The receiver enforces
at least 15 ms between frames. Input devices are `event67` and `event68`.
The hardware interface follows Ulanzi's GPL-3.0 TC002 sample; no vendor binary or
flash image is included in this repository.

## Install and recovery

The clock must already have Wi-Fi configured. Host prerequisites are Python 3,
Google's Android platform-tools (`adb`), and squashfs-tools (`brew install squashfs`
on macOS). The CLI uses the same `install.py` shipped with the compiled resources.

```sh
python3 native/install.py --serial CLOCK_IP:5555 --adb /path/to/adb \
  --backup-dir /private/clock-backup --key-file /private/device.key \
  --pages-file /private/pages --args-file /private/receiver.args --trial
```

`--trial` uses a RAM bind mount and leaves flash unchanged. `--flash` first runs
that trial, then persists the loader after backing up `res`, verifying the exact
TC002 partition profile and rebuilding the filesystem. It verifies every original
file and mode, permits only the loader/config changes, preflights the whole NOR
partition, skips unchanged erase blocks and reads every written block back.
It reboots and waits for the authenticated receiver to return.

Keep the backup directory private and outside version control. It contains vendor
firmware that must not be redistributed. `res-original.bin` is retained unchanged
across upgrades, with SHA-256 and device identity. Keys and entropy seeds never
enter the public repository or the `res` backup.

To temporarily retain the vendor GUI, create `/data/pixelctl.disabled` over ADB
and reboot. Delete that file and reboot to enable pixelctl again.

To restore the exact original application partition:

```sh
python3 native/install.py --serial CLOCK_IP:5555 --adb /path/to/adb \
  --backup-dir /private/clock-backup --restore
```

The device's physical reset button also invokes Ulanzi's factory recovery and
erases user data; the recovered vendor version may be older. If a flash is
interrupted, **keep power connected**. The installer attempts restoration from
the verified original. The low-level `--recover-partial` flag is reserved for an
interrupted write with trusted old/new prefixes; it intentionally bypasses the
usual old-or-new block identity check. Do not use it for normal upgrades.

## Independent HTTPS and entropy

```sh
/data/pixelctl/https-get https://api.github.com/rate_limit \
  --ca /data/pixelctl/cacert.pem
```

The utility returns a JSON envelope with HTTP status, verified hostname, TLS
version and response body. `--token-file PRIVATE_FILE` optionally reads a Bearer
token directly on the device; tokens never appear in process arguments. Redirects
are not followed. No company or GitHub account token is installed by pixelctl.

TLS 1.2 or newer, CA-chain validation and hostname validation are mandatory.
The static build uses Linux `getrandom(GRND_NONBLOCK)` and fails closed before
the kernel CSPRNG is initialized. The TC002 lacks a hardware RNG, so installation
creates a unique 256-bit secret seed from the host OS CSPRNG. At boot, the
supervisor atomically consumes that seed, credits the kernel once per boot, and
atomically stores a fresh CSPRNG seed for the next boot. A failed rotation cannot
reuse a consumed seed. No fixed seed is shipped.

On real TC002 hardware, the utility successfully fetched GitHub over TLS 1.3 and
rejected both a valid certificate for the wrong hostname and a self-signed
certificate. The bundled Mozilla CA snapshot is dated 2026-10-20; its digest and
upstream source are recorded in `third_party/provenance.json`. Update the trust
bundle deliberately as part of maintaining the runtime.

Mbed TLS is used under Apache-2.0; its dual-license text is in
`third_party/mbedtls-LICENSE`. The Mozilla certificate data originates from NSS
under MPL-2.0; see the upstream links inside `cacert.pem`. The bytecode, source,
compiler targets and resource digests are separate from all local device secrets.
