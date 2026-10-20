/*
 * mesen_host.c -- headless host that runs the Mesen2 core and exposes it to
 * the proxy (Proteus.exe) over loopback TCP.
 *
 * Platforms:
 *   - Linux/x86-64 (dlopen, POSIX sockets)
 *   - Windows/x86-64 (LoadLibrary, Winsock)  -> mesen_host.exe
 *
 * Built against a Mesen2 core that exports the extra MMLC hooks added by
 * tools/mesen_host/mesen2_capture.patch:
 *     EnableCaptureRenderer(bool)
 *     GetVideoBuffer(uint32_t* w, uint32_t* h, uint32_t* seq)
 *
 * The core runs its own emulation thread (LoadRom starts it), so this host is
 * a thin pump: it publishes the newest frame + RAM mirror into shared memory
 * (Linux only; the proxy always fetches over TCP) and services commands from
 * the proxy.
 *
 * Build (Linux):   cc -O2 -o mesen_host mesen_host.c -ldl -lpthread
 * Build (Windows): cl /O2 /Fe:mesen_host.exe mesen_host.c ws2_32.lib
 *                  or: x86_64-w64-mingw32-gcc -O2 -o mesen_host.exe \
 *                          mesen_host.c -lws2_32
 * Run:  ./mesen_host --core /path/MesenCore.so [--rom game.nes] [--port 36344]
 *       mesen_host.exe --core C:\path\MesenCore.dll [--rom game.nes]
 *
 * GPLv3: this host links the GPLv3 Mesen2 core and is therefore GPLv3; it is
 * kept separate from the MIT-licensed proxy and never linked into Proteus.exe.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef _WIN32
    #define _CRT_SECURE_NO_WARNINGS
    #include <winsock2.h>
    #include <ws2tcpip.h>
    #include <windows.h>
    #include <direct.h>
    #include <io.h>
    typedef SOCKET sock_t;
    #define BAD_SOCK INVALID_SOCKET
    #define CLOSE_SOCK closesocket
#else
    #include <dlfcn.h>
    #include <unistd.h>
    #include <errno.h>
    #include <signal.h>
    #include <sys/socket.h>
    #include <sys/mman.h>
    #include <netinet/in.h>
    #include <netinet/tcp.h>
    #include <arpa/inet.h>
    #include <fcntl.h>
    typedef int sock_t;
    #define BAD_SOCK (-1)
    #define CLOSE_SOCK close
#endif

#include "mesen_proto.h"

/* ---- Mesen2 C API (subset) ------------------------------------------------ */
typedef void (*fn_void)(void);
typedef void (*fn_init)(const char *, void *, void *, bool, bool, bool, bool);
typedef void (*fn_capture)(bool);
typedef bool (*fn_loadrom)(char *, char *);
typedef bool (*fn_bool)(void);
typedef uint32_t *(*fn_video)(uint32_t *, uint32_t *, uint32_t *);
typedef void (*fn_getmems)(int, uint32_t, uint32_t, uint8_t *);
typedef void (*fn_state)(uint32_t);
typedef void (*fn_dbg)(void);
typedef void (*fn_setinput)(uint32_t, uint32_t);
typedef void (*fn_u32)(uint32_t);
typedef uint32_t (*fn_audio)(int16_t *, uint32_t, uint32_t *, uint32_t *);

typedef struct {
    fn_void InitDll;
    fn_init InitializeEmu;
    fn_capture EnableCaptureRenderer;
    fn_loadrom LoadRom;
    fn_bool IsRunning;
    fn_void Stop;
    fn_void Release;
    fn_video GetVideoBuffer;
    fn_getmems GetMemoryValues;
    fn_state SaveState, LoadState;
    fn_dbg InitializeDebugger;
    fn_setinput SetControllerState;
    fn_void ConfigureNesInput;
    fn_void ResetConsole;
    fn_u32 SetEmulationSpeed;
    fn_void Pause;
    fn_void Resume;
    fn_u32 SetNesOverclock;
    fn_capture EnableCaptureAudio;
    fn_audio GetAudioBuffer;
    fn_void FlushAudioCapture;
} mesen_api;

static mesen_api g_api;
static int g_running = 1;

/* ---- shared memory (Linux only; the proxy uses TCP) ----------------------- */
#ifndef _WIN32
static mesen_frame_shm *g_frame_shm;
static mesen_ram_shm *g_ram_shm;

static void *shm_map(const char *name, size_t size)
{
    int fd = shm_open(name, O_CREAT | O_RDWR, 0600);
    if (fd < 0) { perror("shm_open"); return NULL; }
    if (ftruncate(fd, (off_t)size) != 0) { perror("ftruncate"); close(fd); return NULL; }
    void *p = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (p == MAP_FAILED) { perror("mmap"); return NULL; }
    return p;
}
#endif

#ifndef _WIN32
static void memory_barrier(void)
{
    __sync_synchronize();
}
#endif

/* ---- socket helpers ------------------------------------------------------- */
static int read_full(sock_t fd, void *buf, size_t n)
{
    size_t got = 0;
    while (got < n) {
#ifdef _WIN32
        int r = recv(fd, (char *)buf + got, (int)(n - got), 0);
#else
        ssize_t r = read(fd, (char *)buf + got, n - got);
#endif
        if (r <= 0) return -1;
        got += (size_t)r;
    }
    return 0;
}

static int write_full(sock_t fd, const void *buf, size_t n)
{
    size_t sent = 0;
    while (sent < n) {
#ifdef _WIN32
        int r = send(fd, (const char *)buf + sent, (int)(n - sent), 0);
#else
        ssize_t r = write(fd, (const char *)buf + sent, n - sent);
#endif
        if (r <= 0) return -1;
        sent += (size_t)r;
    }
    return 0;
}

static int send_msg(sock_t fd, uint16_t cmd, uint32_t status,
                    const void *payload, uint32_t len)
{
    mesen_msg_hdr h = { MESEN_PROTO_MAGIC, MESEN_PROTO_VERSION, cmd, len, status };
    if (write_full(fd, &h, sizeof(h)) != 0) return -1;
    if (len && write_full(fd, payload, len) != 0) return -1;
    return 0;
}

/* ---- frame / RAM pump ----------------------------------------------------- */
static void publish_frame(void)
{
#ifndef _WIN32
    if (!g_api.GetVideoBuffer || !g_frame_shm) return;
    uint32_t w = 0, h = 0, seq = 0;
    uint32_t *buf = g_api.GetVideoBuffer(&w, &h, &seq);
    if (!buf || !w || !h) return;
    if (w > MESEN_FRAME_MAX_W) w = MESEN_FRAME_MAX_W;
    if (h > MESEN_FRAME_MAX_H) h = MESEN_FRAME_MAX_H;
    memcpy(g_frame_shm->frame, buf, (size_t)w * h * sizeof(uint32_t));
    g_frame_shm->width = w;
    g_frame_shm->height = h;
    g_frame_shm->pitch = w;
    g_frame_shm->format = 0; /* 0xAARRGGBB */
    memory_barrier();
    g_frame_shm->seq++;
#endif
}

static void publish_ram(void)
{
#ifndef _WIN32
    if (!g_api.GetMemoryValues || !g_ram_shm) return;
    /* MemoryType::NesMemory == 8 in Mesen2's enum */
    g_api.GetMemoryValues(8, 0, 0x800, g_ram_shm->cpu_ram);
    memory_barrier();
    g_ram_shm->seq++;
#endif
}

/* ---- portable temp ROM file ---------------------------------------------- */
static bool make_temp_path(char *path, size_t cap)
{
#ifdef _WIN32
    char dir[MAX_PATH];
    if (!GetTempPathA((DWORD)sizeof(dir), dir)) return false;
    if (!GetTempFileNameA(dir, "mmlc", 0, path)) return false;
    (void)cap;
    return true;
#else
    snprintf(path, cap, "/tmp/mmlc_mesen_rom_XXXXXX");
    int fd = mkstemp(path);
    if (fd < 0) return false;
    close(fd);
    return true;
#endif
}

static void remove_temp(const char *path)
{
#ifdef _WIN32
    DeleteFileA(path);
#else
    unlink(path);
#endif
}

/* ---- command handling ----------------------------------------------------- */
static int handle_load_rom(sock_t fd, const mesen_load_rom *lr, const uint8_t *body)
{
    char path[512];
    if (!make_temp_path(path, sizeof(path)))
        return send_msg(fd, MESEN_CMD_ACK, 1, NULL, 0);

    /* body = prg[prg_size] + chr[chr_size] + 16-byte iNES header prefix */
    uint8_t hdr[16] = { 'N', 'E', 'S', 0x1a,
                        (uint8_t)(lr->prg_size / 0x4000),
                        (uint8_t)(lr->chr_size / 0x2000),
                        (uint8_t)(((lr->mapper & 0x0f) << 4) |
                                  (lr->mirroring ? 1 : 0)),
                        (uint8_t)(lr->mapper & 0xf0) };
    FILE *f = fopen(path, "wb");
    if (!f) { remove_temp(path); return send_msg(fd, MESEN_CMD_ACK, 1, NULL, 0); }
    bool ok = fwrite(hdr, 1, 16, f) == 16;
    if (ok && lr->prg_size) ok = fwrite(body, 1, lr->prg_size, f) == lr->prg_size;
    if (ok && lr->chr_size) ok = fwrite(body + lr->prg_size, 1, lr->chr_size, f) == lr->chr_size;
    if (fclose(f) != 0) ok = false;

    if (g_api.ConfigureNesInput) g_api.ConfigureNesInput();
    if (g_api.FlushAudioCapture) g_api.FlushAudioCapture();
    if (ok) ok = g_api.LoadRom(path, NULL);
    remove_temp(path);
    fprintf(stderr, "[mesen_host] load_rom prg=%u chr=%u mapper=%u -> %d\n",
            lr->prg_size, lr->chr_size, lr->mapper, ok);
    /* The debugger (GetMemoryValues / SetControllerState) requires a loaded
     * console, so (re)initialize it now that a ROM exists. */
    if (ok && g_api.InitializeDebugger)
        g_api.InitializeDebugger();
    return send_msg(fd, MESEN_CMD_ACK, ok ? 0 : 1, NULL, 0);
}

static void serve_client(sock_t fd)
{
    for (;;) {
        mesen_msg_hdr h;
        if (read_full(fd, &h, sizeof(h)) != 0) break;
        if (h.magic != MESEN_PROTO_MAGIC) break;

        uint8_t *payload = NULL;
        if (h.length) {
            payload = malloc(h.length);
            if (!payload || read_full(fd, payload, h.length) != 0) { free(payload); break; }
        }

        switch (h.cmd) {
        case MESEN_CMD_HELLO: {
            uint32_t ver = 0;
            if (g_api.FlushAudioCapture) g_api.FlushAudioCapture();
            send_msg(fd, MESEN_CMD_HELLO_ACK, 0, &ver, sizeof(ver));
            fprintf(stderr, "[mesen_host] hello\n");
            break;
        }
        case MESEN_CMD_LOAD_ROM:
            if (payload && h.length >= sizeof(mesen_load_rom))
                handle_load_rom(fd, (mesen_load_rom *)payload,
                                payload + sizeof(mesen_load_rom));
            break;
        case MESEN_CMD_RUN_FRAME:
            publish_frame();
            publish_ram();
            send_msg(fd, MESEN_CMD_ACK, 0, NULL, 0);
            break;
        case MESEN_CMD_GET_FRAME: {
            uint32_t w = 0, h = 0, seq = 0;
            uint32_t *buf = g_api.GetVideoBuffer ? g_api.GetVideoBuffer(&w, &h, &seq) : NULL;
            if (buf && w && h) {
                uint32_t n = (uint32_t)(8 + (size_t)w * h * 4);
                uint8_t *msg = malloc(n);
                memcpy(msg, &w, 4); memcpy(msg + 4, &h, 4);
                memcpy(msg + 8, buf, (size_t)w * h * 4);
                send_msg(fd, MESEN_CMD_ACK, 0, msg, n);
                free(msg);
            } else {
                send_msg(fd, MESEN_CMD_ACK, 1, NULL, 0);
            }
            break;
        }
        case MESEN_CMD_GET_RAM: {
            uint8_t ram[0x800] = { 0 };
            if (g_api.GetMemoryValues) g_api.GetMemoryValues(8, 0, 0x800, ram);
            send_msg(fd, MESEN_CMD_ACK, 0, ram, sizeof(ram));
            break;
        }
        case MESEN_CMD_SET_INPUT:
            if(payload && h.length >= 8 && g_api.SetControllerState) {
                uint32_t port = *(uint32_t *)payload;
                uint32_t buttons = *(uint32_t *)(payload + 4);
                g_api.SetControllerState(port, buttons);
            }
            send_msg(fd, MESEN_CMD_ACK, 0, NULL, 0);
            break;
        case MESEN_CMD_SAVE_STATE:
            if (payload && h.length >= 4 && g_api.SaveState) g_api.SaveState(*(uint32_t *)payload);
            send_msg(fd, MESEN_CMD_ACK, 0, NULL, 0);
            break;
        case MESEN_CMD_LOAD_STATE:
            if (payload && h.length >= 4 && g_api.LoadState) g_api.LoadState(*(uint32_t *)payload);
            send_msg(fd, MESEN_CMD_ACK, 0, NULL, 0);
            break;
        case MESEN_CMD_RESET:
            if (g_api.ResetConsole) g_api.ResetConsole();
            send_msg(fd, MESEN_CMD_ACK, 0, NULL, 0);
            break;
        case MESEN_CMD_SET_SPEED:
            if (payload && h.length >= 4 && g_api.SetEmulationSpeed)
                g_api.SetEmulationSpeed(*(uint32_t *)payload);
            send_msg(fd, MESEN_CMD_ACK, 0, NULL, 0);
            break;
        case MESEN_CMD_PAUSE:
            if (g_api.Pause) g_api.Pause();
            send_msg(fd, MESEN_CMD_ACK, 0, NULL, 0);
            break;
        case MESEN_CMD_RESUME:
            if (g_api.Resume) g_api.Resume();
            send_msg(fd, MESEN_CMD_ACK, 0, NULL, 0);
            break;
        case MESEN_CMD_SET_OVERCLOCK:
            if (payload && h.length >= 4 && g_api.SetNesOverclock)
                g_api.SetNesOverclock(*(uint32_t *)payload);
            send_msg(fd, MESEN_CMD_ACK, 0, NULL, 0);
            break;
        case MESEN_CMD_GET_AUDIO: {
            static int16_t abuf[16384];
            uint32_t sr = 0, st = 0;
            uint32_t n = g_api.GetAudioBuffer ?
                g_api.GetAudioBuffer(abuf, 16384, &sr, &st) : 0;
            uint32_t bytes = 8 + n * 2;
            uint8_t *msg = malloc(bytes);
            memcpy(msg, &sr, 4);
            memcpy(msg + 4, &st, 4);
            if (n) memcpy(msg + 8, abuf, n * 2);
            send_msg(fd, MESEN_CMD_ACK, 0, msg, bytes);
            free(msg);
            break;
        }
        case MESEN_CMD_SHUTDOWN:
            send_msg(fd, MESEN_CMD_ACK, 0, NULL, 0);
            g_running = 0;
            free(payload);
            return;
        default:
            send_msg(fd, MESEN_CMD_ACK, 2, NULL, 0);
            break;
        }
        free(payload);
        if (!g_running) return;
    }
}

/* ---- dynamic loading ------------------------------------------------------ */
#ifdef _WIN32
static HMODULE load_core(const char *path)
{
    char dir[MAX_PATH];
    strncpy(dir, path, sizeof(dir) - 1);
    dir[sizeof(dir) - 1] = 0;
    char *slash = strrchr(dir, '\\');
    char *fwd = strrchr(dir, '/');
    if (fwd > slash) slash = fwd;
    if (slash) {
        *slash = 0;
        SetDllDirectoryA(dir);
    }
    HMODULE h = LoadLibraryA(path);
    SetDllDirectoryA(NULL);
    if (!h)
        fprintf(stderr, "LoadLibrary(%s) failed: %lu\n", path,
                (unsigned long)GetLastError());
    return h;
}
#define load_symbol(h, n) ((void *)GetProcAddress((HMODULE)(h), (n)))
#else
static void *load_core(const char *path)
{
    void *h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!h) fprintf(stderr, "dlopen: %s\n", dlerror());
    return h;
}
#define load_symbol(h, n) dlsym((h), (n))
#endif

static int load_api(void *core)
{
    g_api.InitDll = (fn_void)load_symbol(core, "InitDll");
    g_api.InitializeEmu = (fn_init)load_symbol(core, "InitializeEmu");
    g_api.EnableCaptureRenderer = (fn_capture)load_symbol(core, "EnableCaptureRenderer");
    g_api.LoadRom = (fn_loadrom)load_symbol(core, "LoadRom");
    g_api.IsRunning = (fn_bool)load_symbol(core, "IsRunning");
    g_api.Stop = (fn_void)load_symbol(core, "Stop");
    g_api.Release = (fn_void)load_symbol(core, "Release");
    g_api.GetVideoBuffer = (fn_video)load_symbol(core, "GetVideoBuffer");
    g_api.GetMemoryValues = (fn_getmems)load_symbol(core, "GetMemoryValues");
    g_api.SaveState = (fn_state)load_symbol(core, "SaveState");
    g_api.LoadState = (fn_state)load_symbol(core, "LoadState");
    g_api.InitializeDebugger = (fn_dbg)load_symbol(core, "InitializeDebugger");
    g_api.SetControllerState = (fn_setinput)load_symbol(core, "SetControllerState");
    g_api.ConfigureNesInput = (fn_void)load_symbol(core, "ConfigureNesInput");
    g_api.ResetConsole = (fn_void)load_symbol(core, "ResetConsole");
    g_api.SetEmulationSpeed = (fn_u32)load_symbol(core, "SetEmulationSpeed");
    g_api.Pause = (fn_void)load_symbol(core, "Pause");
    g_api.Resume = (fn_void)load_symbol(core, "Resume");
    g_api.SetNesOverclock = (fn_u32)load_symbol(core, "SetNesOverclock");
    g_api.EnableCaptureAudio = (fn_capture)load_symbol(core, "EnableCaptureAudio");
    g_api.GetAudioBuffer = (fn_audio)load_symbol(core, "GetAudioBuffer");
    g_api.FlushAudioCapture = (fn_void)load_symbol(core, "FlushAudioCapture");
    if (!g_api.InitDll || !g_api.InitializeEmu || !g_api.LoadRom ||
        !g_api.EnableCaptureRenderer || !g_api.GetVideoBuffer)
        return -1;
    return 0;
}

static const char *default_home(void)
{
#ifdef _WIN32
    static char home[MAX_PATH * 2];
    const char *base = getenv("LOCALAPPDATA");
    if (!base || !*base) base = getenv("TEMP");
    if (!base || !*base) base = ".";
    char parent[MAX_PATH];
    snprintf(parent, sizeof(parent), "%s\\20XX", base);
    snprintf(home, sizeof(home), "%s\\mesen_home", parent);
    _mkdir(parent);
    _mkdir(home);
    return home;
#else
    return "/tmp/mmlc_mesen_home";
#endif
}

int main(int argc, char **argv)
{
    const char *core_path = NULL, *rom = NULL, *home = NULL;
    int port = MESEN_DEFAULT_PORT;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--core") && i + 1 < argc) core_path = argv[++i];
        else if (!strcmp(argv[i], "--rom") && i + 1 < argc) rom = argv[++i];
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--home") && i + 1 < argc) home = argv[++i];
    }
    if (!core_path) {
        fprintf(stderr, "usage: %s --core MesenCore(.so|.dll) [--rom x.nes] "
                        "[--port n] [--home dir]\n", argv[0]);
        return 2;
    }
    if (!home) home = default_home();

#ifdef _WIN32
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        fprintf(stderr, "WSAStartup failed\n");
        return 1;
    }
#else
    signal(SIGPIPE, SIG_IGN);

    g_frame_shm = shm_map(MESEN_FRAME_SHM,
                          sizeof(mesen_frame_shm) + MESEN_FRAME_PIXELS * sizeof(uint32_t));
    g_ram_shm = shm_map(MESEN_RAM_SHM, sizeof(mesen_ram_shm));
    if (!g_frame_shm || !g_ram_shm) return 1;
    g_frame_shm->seq = 0;
    g_ram_shm->seq = 0;
#endif

    void *core = load_core(core_path);
    if (!core) return 1;
    if (load_api(core) != 0) { fprintf(stderr, "core is missing MMLC capture hooks\n"); return 1; }

    g_api.InitDll();
    g_api.EnableCaptureRenderer(true);
    if (g_api.EnableCaptureAudio) g_api.EnableCaptureAudio(true);
    g_api.InitializeEmu(home, NULL, NULL, /*sw*/true, /*noAudio*/true, /*noVideo*/false, /*noInput*/false);
    if (g_api.ConfigureNesInput) g_api.ConfigureNesInput();
    if (rom) {
        g_api.LoadRom((char *)rom, NULL);
        /* The debugger (needed for GetMemoryValues) requires a loaded console. */
        if (g_api.InitializeDebugger) g_api.InitializeDebugger();
    }

    sock_t srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv == BAD_SOCK) { fprintf(stderr, "socket failed\n"); return 1; }
    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, (const char *)&one, sizeof(one));
    setsockopt(srv, IPPROTO_TCP, TCP_NODELAY, (const char *)&one, sizeof(one));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons((uint16_t)port);
    if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        fprintf(stderr, "bind 127.0.0.1:%d failed\n", port);
        return 1;
    }
    listen(srv, 1);
    fprintf(stderr, "[mesen_host] 20XX listening on 127.0.0.1:%d\n", port);

    while (g_running) {
        sock_t cfd = accept(srv, NULL, NULL);
        if (cfd == BAD_SOCK) {
#ifdef _WIN32
            if (WSAGetLastError() == WSAEINTR) continue;
#else
            if (errno == EINTR) continue;
#endif
            break;
        }
        setsockopt(cfd, IPPROTO_TCP, TCP_NODELAY, (const char *)&one, sizeof(one));
        fprintf(stderr, "[mesen_host] client connected\n");
        serve_client(cfd);
        CLOSE_SOCK(cfd);
        /* The game disconnected: stop emulating so the last game doesn't keep
         * running in the background, but stay up in case the proxy reconnects. */
        if (g_running && g_api.Stop)
            g_api.Stop();
    }

    if (g_api.Stop) g_api.Stop();
    if (g_api.Release) g_api.Release();
#ifdef _WIN32
    WSACleanup();
#endif
    return 0;
}
