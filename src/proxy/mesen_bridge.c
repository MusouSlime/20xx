/*
 * mesen_bridge.c -- proxy-side client for the mesen_host process.
 * See src/proxy/mesen_bridge.h and docs/MESEN.md.
 */
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "mesen_bridge.h"
#include "../../tools/mesen_host/mesen_proto.h"

static SOCKET g_sock = INVALID_SOCKET;
static int g_wsa = 0;

/* mesen_bridge_* may be called from the game-object ctor thread (ROM load) and
 * the render thread (frame fetch / input); the protocol is a single request-
 * reply stream on one socket, so serialize access with a spin lock. */
static volatile LONG g_busy = 0;
static void bridge_lock(void)
{
    while (InterlockedExchange(&g_busy, 1))
        Sleep(0);
}
static void bridge_unlock(void)
{
    InterlockedExchange(&g_busy, 0);
}

static int send_all(const void *buf, size_t n)
{
    const char *p = (const char *)buf;
    size_t sent = 0;
    while (sent < n) {
        int r = send(g_sock, p + sent, (int)(n - sent), 0);
        if (r <= 0)
            return -1;
        sent += (size_t)r;
    }
    return 0;
}

static int recv_all(void *buf, size_t n)
{
    char *p = (char *)buf;
    size_t got = 0;
    while (got < n) {
        int r = recv(g_sock, p + got, (int)(n - got), 0);
        if (r <= 0)
            return -1;
        got += (size_t)r;
    }
    return 0;
}

static int send_msg(uint16_t cmd, const void *payload, uint32_t len)
{
    mesen_msg_hdr h;
    h.magic = MESEN_PROTO_MAGIC;
    h.version = MESEN_PROTO_VERSION;
    h.cmd = cmd;
    h.length = len;
    h.status = 0;
    if (send_all(&h, sizeof(h)) != 0)
        return -1;
    if (len && send_all(payload, len) != 0)
        return -1;
    return 0;
}

/* read one reply; returns malloc'd payload (caller frees) or NULL. */
static uint8_t *recv_msg(uint16_t *cmd, uint32_t *status, uint32_t *len)
{
    mesen_msg_hdr h;
    if (recv_all(&h, sizeof(h)) != 0)
        return NULL;
    if (h.magic != MESEN_PROTO_MAGIC)
        return NULL;
    uint8_t *p = NULL;
    if (h.length) {
        p = malloc(h.length);
        if (!p || recv_all(p, h.length) != 0) {
            free(p);
            return NULL;
        }
    }
    if (cmd) *cmd = h.cmd;
    if (status) *status = h.status;
    if (len) *len = h.length;
    return p;
}

int mesen_bridge_connect(const char *host, int port)
{
    if (!g_wsa) {
        WSADATA wsa;
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0)
            return -1;
        g_wsa = 1;
    }
    if (g_sock != INVALID_SOCKET)
        return 0;

    /* No lock: connect happens once from init_thread before any hook that
     * could race is installed. */

    g_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (g_sock == INVALID_SOCKET)
        return -1;

    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons((u_short)port);
    a.sin_addr.s_addr = inet_addr(host ? host : "127.0.0.1");
    if (connect(g_sock, (struct sockaddr *)&a, sizeof(a)) != 0) {
        closesocket(g_sock);
        g_sock = INVALID_SOCKET;
        return -1;
    }

    /* Disable Nagle: the protocol sends a small fixed header followed by a
     * small payload, and the host replies likewise. Without TCP_NODELAY the
     * second write waits for a delayed ACK (~40 ms), which stalls every
     * audio/RAM/input round trip and starves the render thread's frame fetch. */
    {
        int one = 1;
        setsockopt(g_sock, IPPROTO_TCP, TCP_NODELAY, (const char *)&one,
                   sizeof(one));
    }

    if (send_msg(MESEN_CMD_HELLO, NULL, 0) != 0) {
        mesen_bridge_shutdown();
        return -1;
    }
    uint32_t st = 0, len = 0;
    uint8_t *r = recv_msg(NULL, &st, &len);
    free(r);
    if (st != 0) {
        mesen_bridge_shutdown();
        return -1;
    }
    return 0;
}

int mesen_bridge_ready(void)
{
    return g_sock != INVALID_SOCKET;
}

int mesen_bridge_load_rom(const uint8_t *prg, uint32_t prg_size,
                          const uint8_t *chr, uint32_t chr_size,
                          uint32_t mapper, uint32_t mirroring)
{
    if (g_sock == INVALID_SOCKET)
        return -1;
    uint32_t len = (uint32_t)(sizeof(mesen_load_rom) + prg_size + chr_size);
    uint8_t *buf = malloc(len);
    if (!buf)
        return -1;
    mesen_load_rom lr;
    lr.prg_size = prg_size;
    lr.chr_size = chr_size;
    lr.mapper = mapper;
    lr.mirroring = mirroring;
    lr.flags = 0;
    memcpy(buf, &lr, sizeof(lr));
    if (prg_size) memcpy(buf + sizeof(lr), prg, prg_size);
    if (chr_size) memcpy(buf + sizeof(lr) + prg_size, chr, chr_size);

    bridge_lock();
    int rc = send_msg(MESEN_CMD_LOAD_ROM, buf, len);
    free(buf);
    if (rc != 0) {
        bridge_unlock();
        return -1;
    }

    uint32_t st = 0, rlen = 0;
    uint8_t *r = recv_msg(NULL, &st, &rlen);
    free(r);
    bridge_unlock();
    return st == 0 ? 0 : -1;
}

int mesen_bridge_run_frame(void)
{
    if (g_sock == INVALID_SOCKET)
        return -1;
    bridge_lock();
    if (send_msg(MESEN_CMD_RUN_FRAME, NULL, 0) != 0) {
        bridge_unlock();
        return -1;
    }
    uint32_t st = 0, len = 0;
    uint8_t *r = recv_msg(NULL, &st, &len);
    free(r);
    bridge_unlock();
    return st == 0 ? 0 : -1;
}

int mesen_bridge_get_frame(uint32_t *out, int *w, int *h, int out_capacity)
{
    if (g_sock == INVALID_SOCKET)
        return -1;
    bridge_lock();
    if (send_msg(MESEN_CMD_GET_FRAME, NULL, 0) != 0) {
        bridge_unlock();
        return -1;
    }
    uint32_t st = 0, len = 0;
    uint8_t *r = recv_msg(NULL, &st, &len);
    if (!r || st != 0 || len < 8) {
        free(r);
        bridge_unlock();
        return -1;
    }
    uint32_t fw, fh;
    memcpy(&fw, r, 4);
    memcpy(&fh, r + 4, 4);
    int n = (int)(fw * fh);
    if (n > out_capacity) {
        free(r);
        bridge_unlock();
        return -1;
    }
    memcpy(out, r + 8, (size_t)n * 4);
    if (w) *w = (int)fw;
    if (h) *h = (int)fh;
    free(r);
    bridge_unlock();
    return n;
}

int mesen_bridge_get_ram(uint8_t *out, int out_size)
{
    if (g_sock == INVALID_SOCKET)
        return -1;
    bridge_lock();
    if (send_msg(MESEN_CMD_GET_RAM, NULL, 0) != 0) {
        bridge_unlock();
        return -1;
    }
    uint32_t st = 0, len = 0;
    uint8_t *r = recv_msg(NULL, &st, &len);
    if (!r || st != 0) {
        free(r);
        bridge_unlock();
        return -1;
    }
    int n = (int)(len < (uint32_t)out_size ? len : (uint32_t)out_size);
    memcpy(out, r, (size_t)n);
    free(r);
    bridge_unlock();
    return n;
}

int mesen_bridge_set_input(int port, uint32_t buttons)
{
    if (g_sock == INVALID_SOCKET)
        return -1;
    uint32_t payload[2];
    payload[0] = (uint32_t)port;
    payload[1] = buttons;
    bridge_lock();
    if (send_msg(MESEN_CMD_SET_INPUT, payload, sizeof(payload)) != 0) {
        bridge_unlock();
        return -1;
    }
    uint32_t st = 0, len = 0;
    uint8_t *r = recv_msg(NULL, &st, &len);
    free(r);
    bridge_unlock();
    return st == 0 ? 0 : -1;
}

static int state_cmd(uint16_t cmd, uint32_t slot)
{
    if (g_sock == INVALID_SOCKET)
        return -1;
    bridge_lock();
    if (send_msg(cmd, &slot, sizeof(slot)) != 0) {
        bridge_unlock();
        return -1;
    }
    uint32_t st = 0, len = 0;
    uint8_t *r = recv_msg(NULL, &st, &len);
    free(r);
    bridge_unlock();
    return st == 0 ? 0 : -1;
}

int mesen_bridge_save_state(uint32_t slot) { return state_cmd(MESEN_CMD_SAVE_STATE, slot); }
int mesen_bridge_load_state(uint32_t slot) { return state_cmd(MESEN_CMD_LOAD_STATE, slot); }

int mesen_bridge_set_speed(uint32_t percent)
{
    if (g_sock == INVALID_SOCKET)
        return -1;
    bridge_lock();
    if (send_msg(MESEN_CMD_SET_SPEED, &percent, sizeof(percent)) != 0) {
        bridge_unlock();
        return -1;
    }
    uint32_t st = 0, len = 0;
    uint8_t *r = recv_msg(NULL, &st, &len);
    free(r);
    bridge_unlock();
    return st == 0 ? 0 : -1;
}

static int simple_cmd(uint16_t cmd)
{
    if (g_sock == INVALID_SOCKET)
        return -1;
    bridge_lock();
    if (send_msg(cmd, NULL, 0) != 0) {
        bridge_unlock();
        return -1;
    }
    uint32_t st = 0, len = 0;
    uint8_t *r = recv_msg(NULL, &st, &len);
    free(r);
    bridge_unlock();
    return st == 0 ? 0 : -1;
}

int mesen_bridge_pause(void)  { return simple_cmd(MESEN_CMD_PAUSE); }
int mesen_bridge_resume(void) { return simple_cmd(MESEN_CMD_RESUME); }

int mesen_bridge_get_audio(int16_t *out, uint32_t max_samples,
                           uint32_t *sample_rate, uint32_t *stereo)
{
    if (g_sock == INVALID_SOCKET)
        return -1;
    bridge_lock();
    if (send_msg(MESEN_CMD_GET_AUDIO, NULL, 0) != 0) {
        bridge_unlock();
        return -1;
    }
    uint32_t st = 0, len = 0;
    uint8_t *r = recv_msg(NULL, &st, &len);
    if (!r || st != 0 || len < 8) {
        free(r);
        bridge_unlock();
        return -1;
    }
    uint32_t sr, ss;
    memcpy(&sr, r, 4);
    memcpy(&ss, r + 4, 4);
    uint32_t n = (len - 8) / 2;
    if (n > max_samples)
        n = max_samples;
    memcpy(out, r + 8, (size_t)n * 2);
    if (sample_rate) *sample_rate = sr;
    if (stereo) *stereo = ss;
    free(r);
    bridge_unlock();
    return (int)n;
}

int mesen_bridge_set_overclock(uint32_t scanlines)
{
    if (g_sock == INVALID_SOCKET)
        return -1;
    bridge_lock();
    if (send_msg(MESEN_CMD_SET_OVERCLOCK, &scanlines, sizeof(scanlines)) != 0) {
        bridge_unlock();
        return -1;
    }
    uint32_t st = 0, len = 0;
    uint8_t *r = recv_msg(NULL, &st, &len);
    free(r);
    bridge_unlock();
    return st == 0 ? 0 : -1;
}

int mesen_bridge_reset(void)
{
    if (g_sock == INVALID_SOCKET)
        return -1;
    bridge_lock();
    if (send_msg(MESEN_CMD_RESET, NULL, 0) != 0) {
        bridge_unlock();
        return -1;
    }
    uint32_t st = 0, len = 0;
    uint8_t *r = recv_msg(NULL, &st, &len);
    free(r);
    bridge_unlock();
    return st == 0 ? 0 : -1;
}

void mesen_bridge_shutdown(void)
{
    if (g_sock != INVALID_SOCKET) {
        bridge_lock();
        send_msg(MESEN_CMD_SHUTDOWN, NULL, 0);
        uint32_t st = 0, len = 0;
        uint8_t *r = recv_msg(NULL, &st, &len);
        free(r);
        closesocket(g_sock);
        g_sock = INVALID_SOCKET;
        bridge_unlock();
    }
    if (g_wsa) {
        WSACleanup();
        g_wsa = 0;
    }
}
