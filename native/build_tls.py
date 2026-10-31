#!/usr/bin/env python3
"""Build the pinned, static TC002 HTTPS utility outside the source tree."""
import argparse
import hashlib
from pathlib import Path
import shlex
import shutil
import subprocess
import urllib.request

VERSION = "3.6.7"
URL = f"https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-{VERSION}/mbedtls-{VERSION}.tar.bz2"
SHA256 = "a7e8bcbec0e6f761b4af24f25677626b35f762f68eef79c08677a363212d11f6"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True)
    parser.add_argument("--zig", default="zig")
    args = parser.parse_args()
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    archive = work / f"mbedtls-{VERSION}.tar.bz2"
    if not archive.exists():
        urllib.request.urlretrieve(URL, archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("Pinned Mbed TLS archive checksum mismatch")
    source = work / f"mbedtls-{VERSION}"
    source.mkdir(exist_ok=True)
    subprocess.run(["tar", "-xf", str(archive), "-C", str(source), "--strip-components", "1"], check=True)
    zig = shutil.which(args.zig) or str(Path(args.zig).resolve())
    target = "-target arm-linux-musleabihf -mcpu=cortex_a7"
    flags = "-Os -ffunction-sections -fdata-sections -DMBEDTLS_NO_PLATFORM_ENTROPY -DMBEDTLS_ENTROPY_HARDWARE_ALT"
    subprocess.run(["make", "-C", str(source), "clean"], check=True)
    subprocess.run(["make", "-C", str(source), "lib", "-j4", f"CC={shlex.quote(zig)} cc {target}", f"AR={shlex.quote(zig)} ar", f"CFLAGS={flags}"], check=True)
    native = Path(__file__).resolve().parent
    command = [zig, "cc", *shlex.split(target), *shlex.split(flags), "-static", "-Wl,--gc-sections", "-I" + str(source / "include"), str(native / "https_get.c"), str(native / "entropy_linux.c")]
    command.extend(str(source / "library" / name) for name in ["libmbedtls.a", "libmbedx509.a", "libmbedcrypto.a"])
    subprocess.run(command + ["-o", str(native / "https-get-armv7")], check=True)


if __name__ == "__main__":
    main()
