/* SPDX-License-Identifier: GPL-3.0-or-later
 * EasyUI startup shim: preserve vendor hardware initialization and provide a
 * reversible hand-off to the standalone pixelctl display application.
 */
#define _POSIX_C_SOURCE 200809L
#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/types.h>
#include <unistd.h>
static void *vendor;
static int launched;
static void ensure_vendor(void) {
    if (!vendor) vendor = dlopen("/res/lib/libzkgui.so", RTLD_NOW | RTLD_GLOBAL);
    if (!vendor) fprintf(stderr, "pixelctl: vendor loader unavailable: %s\n", dlerror());
}
void onEasyUIInit(void *context) {
    ensure_vendor();
    if (vendor) {
        void (*init)(void *) = dlsym(vendor, "onEasyUIInit");
        if (init) init(context);
    }
}
void onEasyUIDeinit(void *context) {
    if (vendor) {
        void (*deinit)(void *) = dlsym(vendor, "onEasyUIDeinit");
        if (deinit) deinit(context);
    }
}
const char *onStartupApp(void *context) {
    ensure_vendor();
    const char *name = "mainActivity";
    if (vendor) {
        const char *(*startup)(void *) = dlsym(vendor, "onStartupApp");
        if (startup) name = startup(context);
    }
    if (!launched && access("/data/pixelctl.disabled", F_OK) != 0 &&
        access("/data/pixelctl/receiver", X_OK) == 0 &&
        access("/data/pixelctl/supervisor", X_OK) == 0 &&
        access("/data/pixelctl.key", R_OK) == 0) {
        launched = 1;
        char parent[32]; snprintf(parent, sizeof(parent), "%ld", (long)getpid());
        pid_t child = fork();
        if (child == 0) {
            setsid();
            int fd = open("/tmp/pixelctl-native.log", O_WRONLY | O_CREAT | O_TRUNC, 0600);
            if (fd >= 0) { dup2(fd, 1); dup2(fd, 2); close(fd); }
            close(0);
            execl("/data/pixelctl/supervisor", "pixelctl-supervisor", parent, "15", "/data/pixelctl/receiver", "/data/pixelctl/frames", "/data/pixelctl.key", "/data/pixelctl/receiver.args", (char *)NULL);
            _exit(127);
        }
    }
    return name;
}
