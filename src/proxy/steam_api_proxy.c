/*
 * steam_api_proxy.c -- MMLC1 (Mega Man Legacy Collection) patch-only proxy.
 *
 * Drop-in replacement for the game's steam_api.dll. Re-exports the full
 * original export surface (via the generated .def, forwarding to
 * steam_api_orig.dll) and, on load, applies runtime patches to Proteus.exe:
 *
 *   nop_overlay=1  NOP the 0xDF callee-ret stub writer (pattern C6 04 08 DF)
 *   scan=1         AOB-scan for the NESSystem virtuals and log their VAs
 *   rom_inject=1   hook the per-game overlay writer and copy a same-size ROM
 *                  from rom_dir/<key>.bin over the in-memory ROM buffer
 *   rom_dir=roms   directory (next to Proteus.exe) holding injection blobs
 *   log=1          append a line to mmlc_proxy.log next to the main module
 *
 * The DLL never contains or redistributes any Capcom code or ROM data. It
 * only modifies the process image of the user's own install in memory.
 *
 * Build: see build/build_proxy.sh
 */
#include <windows.h>
#include <mmsystem.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mesen_bridge.h"

#define PAT0 0xC6
#define PAT1 0x04
#define PAT2 0x08
#define PAT3 0xDF

/* AOB signatures of the NESSystem virtuals, derived from the unpacked
 * Proteus.exe (image base 0x400000).  These are position independent: the
 * constants baked into the instructions are struct offsets / magic values,
 * not relocatable addresses.  See docs/RE_FACTS.md for the static analysis.
 *
 *   bank_xlate  VA 0x4645a0  vtable slot 3  (16-bit addr -> banked offset)
 *   read6502    VA 0x4645e0  vtable slot 2  (thunk -> body 0x464640)
 *   advance     VA 0x460750  vtable slot 5  (run a frame's worth of cycles)
 */
#define SIG_BANKXLATE \
    "66 8B 54 24 04 B8 00 C0 00 00 66 3B D0 B8 07 00 00 00 73 06 8B 81 E4 13 05 00"
#define SIG_READ6502 \
    "8B 54 24 04 53 56 BB 00 C0 00 00 8B F1 57 66 3B D3"
#define SIG_ADVANCE \
    "56 8B F1 8B 8E 78 0D 05 00 81 F9 55 74 00 00 7D 1D"

/* FUN_0045c220 -- the per-instance overlay writer, called once per patch site
 * right after the ctor memcpy's the ROM into the heap buffer. Unique body:
 *   mov ecx,[ecx+0x81C]; mov eax,[esp+4]; <store>; ret 4
 * We fully replace this tiny function with our own handler. */
#define SIG_OVERLAY_FN "8B 89 1C 08 00 00 8B 44 24 04"

/* FUN_0046c5c0 -- the asset reader: try archive, then loose file. Hooking it
 * lets us observe (and override) every asset load, including Museum art. */
#define SIG_ASSET "56 8B 74 24 08 85 F6 0F 95 C0 84 C0 74 3E"

/* Per-game ROM source blob (this+0x810) as an RVA, for US builds. The ctor
 * sets [this+0x810] to the .rdata PRG source = file_off + 0x1200. */
typedef struct {
    const char *key;
    unsigned long src_rva;
    unsigned long prg_size;
    unsigned char mapper;     /* iNES mapper, for the Mesen host */
    unsigned char mirroring;  /* 0=h, 1=v */
} rom_src_t;

/* prg_size is baked in because the ctor sets [this+0x814] only *after* the
 * overlay calls, so it is not yet valid inside the hook. */
static const rom_src_t ROM_SRCS[] = {
    { "mm1", 0x2b04b0UL, 0x20000UL, 2, 1 }, { "mm2", 0x0090370UL, 0x40000UL, 1, 0 },
    { "mm3", 0x0d03b0UL, 0x40000UL, 4, 0 }, { "mm4", 0x1303f0UL, 0x80000UL, 4, 0 },
    { "mm5", 0x1b0430UL, 0x40000UL, 4, 0 }, { "mm6", 0x230470UL, 0x80000UL, 4, 0 },
    { "rk1", 0x513848UL, 0x20000UL, 2, 1 }, { "rk2", 0x2f3708UL, 0x40000UL, 1, 0 },
    { "rk3", 0x333748UL, 0x40000UL, 4, 0 }, { "rk4", 0x393788UL, 0x80000UL, 4, 0 },
    { "rk5", 0x4137c8UL, 0x40000UL, 4, 0 }, { "rk6", 0x493808UL, 0x80000UL, 4, 0 },
};
#define ROM_SRCS_COUNT ((int)(sizeof(ROM_SRCS) / sizeof(ROM_SRCS[0])))

static void log_line(const char *fmt, ...);

static HMODULE g_self = NULL;
static char g_dir[MAX_PATH];
static char g_log[MAX_PATH];
static char g_romdir[MAX_PATH];
static DWORD g_base = 0;
static DWORD g_patched_inst[32];
static int g_inst_ridx[32];   /* ROM_SRCS index per patched instance */
static int g_npatched = 0;
static int g_rom_inject = 0;
static int g_asset_log = 0;
void *g_tramp_asset = NULL;  /* read by the asm hook stub */

/* Phase 6 (docs/MESEN.md): run the game through the x64 Mesen host instead of
 * the Eclipse core. `mesen=1` connects to mesen_host and loads the ROM; the
 * video/input override is wired in M6.3. */
static int g_mesen = 0;
static char g_mesen_host[64] = "127.0.0.1";
static int g_mesen_port = 36344;
static int g_mesen_loaded[16];  /* per-ROM_SRCS index */
/* Defer the host ROM load until the game's NES screen actually appears (the
 * MMLC front-end never composites the 4x screen, so this avoids running Mesen
 * over the menus). mesen_note_game_screen() is called from the D3D hook. */
static void mesen_load_active(void);

/* The front-end preloads every game instance but only steps the one it is
 * previewing/playing. Hook each game's `advance` (vtable slot 5) to record that
 * instance so the proxy loads the correct game's ROM into Mesen. */
volatile DWORD g_active_inst = 0;   /* written by the advance stubs */
volatile DWORD g_advance_count = 0; /* incremented by the advance stubs */
static int g_loaded_ridx = -1;   /* ROM_SRCS index currently in Mesen */
static volatile int g_ui_paused = 0;   /* Mesen paused for the MMLC UI */

/* Option-2 framebuffer probe: dump the NESSystem object to a file so the
 * engine's video buffer offset/format can be located offline. */
static int g_mesen_probe = 0;
static DWORD g_probe_this = 0;
static volatile LONG g_probe_started = 0;
#define PROBE_DUMP_SIZE 0x90000UL

/* Mesen video substitution.
 *
 * The NES screen is NOT uploaded through TextureManagerDX11::UpdateTexture
 * (slot 9, 0x47bec0) -- that only fires ~1-3x/session on a 64x64 texture. The
 * per-frame screen instead goes through the D3D11 immediate context: the
 * renderer Maps a dynamic texture, writes the frame into the returned CPU
 * pointer, then Unmaps. We therefore hook ID3D11DeviceContext::Map/Unmap on
 * the engine's immediate context and substitute Mesen's frame on the way out.
 *
 * Renderer singleton: [g_base+0x5cf174] (RVA 0x5cf174). Its constructor
 * (VA 0x47c4c0) calls the D3D11CreateDeviceAndSwapChain wrapper (VA 0x47cd40)
 * with ppDevice = this+0x60 and ppImmediateContext = this+0x64, so:
 *     ID3D11Device*        @ [obj+0x60]
 *     ID3D11DeviceContext* @ [obj+0x64]
 * ID3D11DeviceContext vtable: Map = +0x38, Unmap = +0x3c. */
#define MESEN_TEX_RVA 0x7bec0UL
#define MESEN_RENDERER_RVA 0x5cf174UL

static BYTE g_mesen_frame[256 * 240 * 4];
static volatile int g_mesen_frame_ok = 0;
void *g_tramp_tex = NULL;
extern void mesen_tex_hook(void);

static int g_tex_logs = 0;

/* Pull the newest frame from the Mesen host into g_mesen_frame. */
static void mesen_fetch_frame(void)
{
    if (!g_mesen || !mesen_bridge_ready())
        return;
    int w = 0, h = 0;
    int n = mesen_bridge_get_frame((uint32_t *)g_mesen_frame, &w, &h, 256 * 240);
    if (n > 0 && w == 256 && h == 240)
        g_mesen_frame_ok = 1;
}

void *mesen_tex_data(void *tex, void *data)
{
    if (g_mesen && mesen_bridge_ready()) {
        mesen_fetch_frame();
        if (g_tex_logs < 6) {
            g_tex_logs++;
            log_line("[mmlc] tex hook: ok=%d", g_mesen_frame_ok);
        }
    }
    if (g_mesen_frame_ok && tex) {
        float w = *(float *)((char *)tex + 4);
        float h = *(float *)((char *)tex + 8);
        if (g_tex_logs < 400) {
            g_tex_logs++;
            log_line("[mmlc] tex hook: tex %gx%g -> %s", w, h,
                     ((int)w == 256 && (int)h == 240) ? "MESEN" : "orig");
        }
        if ((int)w == 256 && (int)h == 240)
            return g_mesen_frame;
    }
    return data;
}

/* --- D3D11 immediate-context Map/Unmap hook (M6.3) ---------------------- */

typedef struct {
    void *pData;
    UINT RowPitch;
    UINT DepthPitch;
} mesen_mapped_subresource;

typedef HRESULT (WINAPI *mesen_map_fn)(void *This, void *res, UINT sub,
                                       UINT maptype, UINT flags, void *out);
typedef void (WINAPI *mesen_unmap_fn)(void *This, void *res, UINT sub);

static mesen_map_fn g_real_d3d_map = NULL;
static mesen_unmap_fn g_real_d3d_unmap = NULL;

/* One entry per outstanding Write-style Map we may need to patch on Unmap. */
typedef struct {
    void *res;
    void *pData;
    UINT rowpitch;
    UINT depthpitch;
} mesen_map_slot;
static mesen_map_slot g_map_slots[8];
static int g_map_logs = 0;
static DWORD g_last_4x_tick = 0;
static int g_4x_logs = 0;

/* M6.4 experiment: feed Mesen's CPU RAM into the engine's NES RAM
 * (NESSystem+0x0b, vtable 0x6d04b4 at +0) each frame so the wrapper's logic
 * follows Mesen. Opt-in via `mesen_ram_sync=1`. */
static int g_ram_sync = 0;
static DWORD g_nes_inst = 0;
static uint8_t g_mesen_ram[0x800];
#define MESEN_MM1_VTABLE 0x6d04b4UL
#define MESEN_NES_RAM_OFF 0x0bUL

static void mesen_sync_ram(void)
{
    if (!g_ram_sync || !g_nes_inst || !g_mesen || !mesen_bridge_ready())
        return;
    if (*(volatile DWORD *)(ULONG_PTR)g_nes_inst != MESEN_MM1_VTABLE)
        return;
    if (mesen_bridge_get_ram(g_mesen_ram, sizeof(g_mesen_ram)) <= 0)
        return;
    memcpy((void *)(ULONG_PTR)(g_nes_inst + MESEN_NES_RAM_OFF),
           g_mesen_ram, sizeof(g_mesen_ram));
}

/* Nearest-neighbour scale of Mesen's 256x240 frame into a dstw x dsth BGRA
 * buffer. Used for the engine's 1024x960 (4x) composited screen buffer. */
static void mesen_blit_scaled(BYTE *dst, UINT pitch, int dw, int dh)
{
    if (!g_mesen_frame_ok || !dst || dw <= 0 || dh <= 0)
        return;
    const uint32_t *src = (const uint32_t *)g_mesen_frame;
    /* Fast path: exact 4x upscale (256x240 -> 1024x960), no per-pixel divide. */
    if (dw == 1024 && dh == 960) {
        for (int y = 0; y < 240; y++) {
            BYTE *d0 = dst + (size_t)(y * 4) * pitch;
            const uint32_t *srow = src + y * 256;
            for (int r = 0; r < 4; r++) {
                uint32_t *row = (uint32_t *)(d0 + (size_t)r * pitch);
                for (int x = 0; x < 256; x++) {
                    uint32_t v = srow[x];
                    row[x * 4] = v; row[x * 4 + 1] = v;
                    row[x * 4 + 2] = v; row[x * 4 + 3] = v;
                }
            }
        }
        return;
    }
    for (int y = 0; y < dh; y++) {
        int sy = y * 240 / dh;
        uint32_t *row = (uint32_t *)(dst + (size_t)y * pitch);
        const uint32_t *srow = src + sy * 256;
        for (int x = 0; x < dw; x++)
            row[x] = srow[x * 256 / dw];
    }
}

/* True if ptr lies inside the currently loaded d3d11.dll image. */
static int ptr_in_module(DWORD ptr, const char *mod)
{
    HMODULE m = GetModuleHandleA(mod);
    if (!m)
        return 0;
    BYTE *b = (BYTE *)m;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)b;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE)
        return 0;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(b + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE)
        return 0;
    return ptr >= (DWORD)(ULONG_PTR)b &&
           ptr < (DWORD)(ULONG_PTR)b + nt->OptionalHeader.SizeOfImage;
}

/* Dedupe table so one run logs every distinct resource/pitch once. */
typedef struct { DWORD a, b, c; } mesen_seen;
static int seen_has(const mesen_seen *t, int n, DWORD a, DWORD b, DWORD c)
{
    for (int i = 0; i < n; i++)
        if (t[i].a == a && t[i].b == b && t[i].c == c)
            return 1;
    return 0;
}
static int seen_add(mesen_seen *t, int *n, int max, DWORD a, DWORD b, DWORD c)
{
    if (*n >= max || seen_has(t, *n, a, b, c))
        return 0;
    t[*n].a = a; t[*n].b = b; t[*n].c = c;
    (*n)++;
    return 1;
}

static mesen_seen g_map_seen[128];
static int g_map_seen_n = 0;

static HRESULT WINAPI hook_d3d_map(void *This, void *res, UINT sub,
                                   UINT maptype, UINT flags, void *out)
{
    HRESULT hr = g_real_d3d_map(This, res, sub, maptype, flags, out);
    mesen_mapped_subresource *m = (mesen_mapped_subresource *)out;
    if (hr >= 0 && m) {
        if (seen_add(g_map_seen, &g_map_seen_n, 128,
                     (DWORD)(ULONG_PTR)res, maptype, m->RowPitch)) {
            log_line("[mmlc] d3d Map res=%p sub=%u type=%u flags=%u pData=%p "
                     "row=%u depth=%u", res, sub, maptype, flags, m->pData,
                     m->RowPitch, m->DepthPitch);
        }
        /* A 256x240 BGRA texture has RowPitch 1024, DepthPitch 245760; the
         * engine's 4x composited screen is 1024x960 (RowPitch 4096,
         * DepthPitch 3932160). Buffers have RowPitch == DepthPitch. */
        int is_nes = m->RowPitch == 256 * 4 &&
                     (m->DepthPitch == 0 || m->DepthPitch == (UINT)(256 * 4 * 240));
        int is_screen = m->RowPitch == 4096 && m->DepthPitch == 3932160u;
        if (is_nes || is_screen) {
            /* Both the 256x240 preview texture and the 4x play screen show the
             * game the front-end is stepping; keep Mesen loaded with it. */
            mesen_load_active();
            mesen_fetch_frame();
            if (g_mesen_frame_ok) {
                for (int i = 0; i < 8; i++) {
                    if (g_map_slots[i].res == res || g_map_slots[i].res == NULL) {
                        g_map_slots[i].res = res;
                        g_map_slots[i].pData = m->pData;
                        g_map_slots[i].rowpitch = m->RowPitch;
                        g_map_slots[i].depthpitch = m->DepthPitch;
                        break;
                    }
                }
            }
        }
    }
    return hr;
}

static void WINAPI hook_d3d_unmap(void *This, void *res, UINT sub)
{
    if (g_mesen_frame_ok) {
        for (int i = 0; i < 8; i++) {
            if (g_map_slots[i].res == res && g_map_slots[i].pData) {
                UINT rp = g_map_slots[i].rowpitch;
                UINT dp = g_map_slots[i].depthpitch;
                if (rp == 256 * 4) {
                    memcpy(g_map_slots[i].pData, g_mesen_frame, 256 * 240 * 4);
                    if (g_map_logs < 64) {
                        g_map_logs++;
                        log_line("[mmlc] d3d Unmap res=%p -> Mesen 256x240 "
                                 "(pData=%p)", res, g_map_slots[i].pData);
                    }
                } else if (rp == 4096 && dp == 3932160u) {
                    DWORD now = GetTickCount();
                    DWORD dt = g_last_4x_tick ? now - g_last_4x_tick : 0;
                    g_last_4x_tick = now;
                    DWORD t0 = GetTickCount();
                    mesen_blit_scaled((BYTE *)g_map_slots[i].pData, rp, 1024, 960);
                    mesen_sync_ram();
                    DWORD blit = GetTickCount() - t0;
                    if (g_4x_logs < 30) {
                        g_4x_logs++;
                        log_line("[mmlc] d3d Unmap 4x dt=%lu ms blit=%lu ms pData=%p",
                                 dt, blit, g_map_slots[i].pData);
                    }
                }
                g_map_slots[i].res = NULL;
                g_map_slots[i].pData = NULL;
                break;
            }
        }
    }
    g_real_d3d_unmap(This, res, sub);
}

/* UpdateSubresource is the most likely path for a *separate* NES framebuffer
 * allocation to reach the screen texture. The frame is the source argument, so
 * substitution is a clean pointer swap -- "point it at Mesen's frame". */
typedef void (WINAPI *mesen_updsub_fn)(void *This, void *dst, UINT dstSub,
                                       const void *box, const void *src,
                                       UINT srcRowPitch, UINT srcDepthPitch);
static mesen_updsub_fn g_real_d3d_updsub = NULL;
static mesen_seen g_updsub_seen[128];
static int g_updsub_seen_n = 0;

static void WINAPI hook_d3d_update_subresource(void *This, void *dst,
        UINT dstSub, const void *box, const void *src, UINT srcRowPitch,
        UINT srcDepthPitch)
{
    if (seen_add(g_updsub_seen, &g_updsub_seen_n, 128,
                 (DWORD)(ULONG_PTR)dst, srcRowPitch, srcDepthPitch)) {
        log_line("[mmlc] d3d UpdateSubresource dst=%p src=%p sub=%u row=%u "
                 "depth=%u box=%p", dst, src, dstSub, srcRowPitch,
                 srcDepthPitch, box);
    }
    if (srcRowPitch == 256 * 4 &&
        (srcDepthPitch == 0 || srcDepthPitch == (UINT)(256 * 4 * 240))) {
        mesen_fetch_frame();
        if (g_mesen_frame_ok) {
            log_line("[mmlc] d3d UpdateSubresource SUBSTITUTE dst=%p -> Mesen",
                     dst);
            g_real_d3d_updsub(This, dst, dstSub, box, g_mesen_frame,
                              srcRowPitch, srcDepthPitch);
            return;
        }
    }
    g_real_d3d_updsub(This, dst, dstSub, box, src, srcRowPitch, srcDepthPitch);
}

/* CopySubresourceRegion / CopyResource: log only, to catch a screen copy that
 * bypasses UpdateSubresource. */
typedef void (WINAPI *mesen_copysub_fn)(void *This, void *dst, UINT dstSub,
        UINT dx, UINT dy, UINT dz, void *src, UINT srcSub, const void *srcBox);
typedef void (WINAPI *mesen_copyres_fn)(void *This, void *dst, void *src);
static mesen_copysub_fn g_real_d3d_copysub = NULL;
static mesen_copyres_fn g_real_d3d_copyres = NULL;
static mesen_seen g_copy_seen[128];
static int g_copy_seen_n = 0;

static void WINAPI hook_d3d_copy_subresource_region(void *This, void *dst,
        UINT dstSub, UINT dx, UINT dy, UINT dz, void *src, UINT srcSub,
        const void *srcBox)
{
    if (seen_add(g_copy_seen, &g_copy_seen_n, 128,
                 (DWORD)(ULONG_PTR)dst, (DWORD)(ULONG_PTR)src, dx)) {
        log_line("[mmlc] d3d CopySubRegion dst=%p src=%p d=(%u,%u) box=%p",
                 dst, src, dx, dy, srcBox);
    }
    g_real_d3d_copysub(This, dst, dstSub, dx, dy, dz, src, srcSub, srcBox);
}

static void WINAPI hook_d3d_copy_resource(void *This, void *dst, void *src)
{
    if (seen_add(g_copy_seen, &g_copy_seen_n, 128,
                 (DWORD)(ULONG_PTR)dst, (DWORD)(ULONG_PTR)src, 0xFFFFFFFFu)) {
        log_line("[mmlc] d3d CopyResource dst=%p src=%p", dst, src);
    }
    g_real_d3d_copyres(This, dst, src);
}

/* ID3D11Device::CreateTexture2D / CreateBuffer: enumerate what the engine
 * allocates. The NES screen may be created with initial data rather than
 * updated, which would explain the missing Map/UpdateSubresource uploads. */
typedef HRESULT (WINAPI *mesen_create_tex2d_fn)(void *This, const void *desc,
                                                const void *init, void **out);
typedef HRESULT (WINAPI *mesen_create_buf_fn)(void *This, const void *desc,
                                              const void *init, void **out);
static mesen_create_tex2d_fn g_real_create_tex2d = NULL;
static mesen_create_buf_fn g_real_create_buf = NULL;
static mesen_seen g_tex_seen[128];
static int g_tex_seen_n = 0;
static mesen_seen g_buf_seen[128];
static int g_buf_seen_n = 0;

/* D3D11_TEXTURE2D_DESC fields we care about (all UINT). */
#define T2D_W 0
#define T2D_H 1
#define T2D_MIP 2
#define T2D_ARR 3
#define T2D_FMT 4
#define T2D_SAMPC 5
#define T2D_USAGE 7
#define T2D_BIND 8
#define T2D_CPU 9

static HRESULT WINAPI hook_d3d_create_texture2d(void *This, const void *desc,
                                                const void *init, void **out)
{
    const UINT *d = (const UINT *)desc;
    int is_nes = d && d[T2D_W] == 256 && d[T2D_H] == 240;
    if (is_nes && init) {
        mesen_fetch_frame();
        if (g_mesen_frame_ok) {
            /* D3D11_SUBRESOURCE_DATA: { pSysMem, SysMemPitch, SlicePitch }. */
            UINT *ini = (UINT *)init;
            ini[0] = (UINT)(ULONG_PTR)g_mesen_frame;
            ini[1] = 256 * 4;
            log_line("[mmlc] d3d CreateTexture2D NES substitute -> Mesen");
        }
    }
    HRESULT hr = g_real_create_tex2d(This, desc, init, out);
    if (hr >= 0 && d &&
        seen_add(g_tex_seen, &g_tex_seen_n, 128, d[T2D_W], d[T2D_H], d[T2D_FMT])) {
        const UINT *ini = (const UINT *)init;  /* pSysMem, pitch, slicepitch */
        log_line("[mmlc] d3d CreateTexture2D %ux%u fmt=%u mip=%u arr=%u "
                 "usage=%u bind=%#x cpu=%#x init=%p pitch=%u -> %p%s",
                 d[T2D_W], d[T2D_H], d[T2D_FMT], d[T2D_MIP], d[T2D_ARR],
                 d[T2D_USAGE], d[T2D_BIND], d[T2D_CPU], ini ? ini[0] : 0,
                 ini ? ini[1] : 0, out ? *out : 0, is_nes ? " NES?" : "");
    }
    return hr;
}

static HRESULT WINAPI hook_d3d_create_buffer(void *This, const void *desc,
                                             const void *init, void **out)
{
    HRESULT hr = g_real_create_buf(This, desc, init, out);
    const UINT *d = (const UINT *)desc;
    if (hr >= 0 && d &&
        seen_add(g_buf_seen, &g_buf_seen_n, 128, d[0], d[1], d[2])) {
        log_line("[mmlc] d3d CreateBuffer size=%u usage=%u bind=%#x cpu=%#x "
                 "stride=%u -> %p", d[0], d[1], d[2], d[3], d[5],
                 out ? *out : 0);
    }
    return hr;
}

static int install_d3d_device_hook(DWORD dev)
{
    DWORD *vt = *(DWORD **)(ULONG_PTR)dev;
    if (!vt) {
        log_line("[mmlc] d3d device hook: dev %08lx has no vtable", dev);
        return -1;
    }
    DWORD cb = vt[0x0c / 4], ct2 = vt[0x14 / 4];
    log_line("[mmlc] d3d device hook: dev=%08lx vt=%08lx CreateBuffer=%08lx(%s) "
             "CreateTexture2D=%08lx(%s)", dev, (DWORD)(ULONG_PTR)vt, cb,
             ptr_in_module(cb, "d3d11.dll") ? "d3d11" : "?",
             ct2, ptr_in_module(ct2, "d3d11.dll") ? "d3d11" : "?");
    if (!ptr_in_module(ct2, "d3d11.dll"))
        return -1;
    DWORD old;
    /* patch 0x0c .. 0x14 inclusive = 0x0c bytes. */
    if (!VirtualProtect(&vt[0x0c / 4], 0x0c, PAGE_EXECUTE_READWRITE, &old)) {
        log_line("[mmlc] d3d device hook: VirtualProtect failed (%lu)",
                 GetLastError());
        return -1;
    }
    if (ptr_in_module(cb, "d3d11.dll")) {
        g_real_create_buf = (mesen_create_buf_fn)cb;
        vt[0x0c / 4] = (DWORD)(ULONG_PTR)hook_d3d_create_buffer;
    }
    g_real_create_tex2d = (mesen_create_tex2d_fn)ct2;
    vt[0x14 / 4] = (DWORD)(ULONG_PTR)hook_d3d_create_texture2d;
    VirtualProtect(&vt[0x0c / 4], 0x0c, old, &old);
    FlushInstructionCache(GetCurrentProcess(), &vt[0x0c / 4], 0x0c);
    log_line("[mmlc] d3d CreateTexture2D/CreateBuffer hook installed");
    return 0;
}

/* Phase 6 handoff: stop the engine's own NES emulation so Mesen is the single
 * source of truth. advance (vtable slot 5, 0x460750) runs one frame of CPU
 * cycles; replacing its first byte with RET makes it a no-op (the engine then
 * only renders, and we substitute Mesen's frame into the screen buffer). */
static int g_mesen_freeze = 1;
static BYTE *g_advance_fn = NULL;

static int aob_scan(HMODULE mod, const char *sig, void **out, int max);

static int install_advance_freeze(HMODULE mod)
{
    void *hits[4];
    int n = aob_scan(mod, SIG_ADVANCE, hits, 4);
    if (n != 1) {
        log_line("[mmlc] freeze: %d advance match(es) (not installed)", n);
        return -1;
    }
    BYTE *fn = (BYTE *)hits[0];
    DWORD old;
    if (!VirtualProtect(fn, 1, PAGE_EXECUTE_READWRITE, &old)) {
        log_line("[mmlc] freeze: VirtualProtect failed (%lu)", GetLastError());
        return -1;
    }
    BYTE orig = fn[0];
    fn[0] = 0xC3;  /* ret */
    VirtualProtect(fn, 1, old, &old);
    FlushInstructionCache(GetCurrentProcess(), fn, 1);
    g_advance_fn = fn;
    log_line("[mmlc] advance frozen at %p (was %02x)", fn, orig);
    return 0;
}

/* Each game subclass has its own `advance` in vtable slot 5 (MM1/2 use the
 * base 0x460750; MM3-6/RK3-6 override it). Byte-patching the base misses them,
 * so patch slot 5 of every game vtable to a tiny stub that records the stepped
 * instance and jumps to the original advance. */
static const DWORD GAME_VTABLE_RVAS[] = {
    0x2d04b4, /* mm1 */ 0x0d0374, /* mm2 */ 0x1303b4, /* mm3 */
    0x1b03f4, /* mm4 */ 0x230434, /* mm5 */ 0x2b0474, /* mm6 */
    0x53384c, /* rk1 */ 0x33370c, /* rk2 */ 0x39374c, /* rk3 */
    0x41378c, /* rk4 */ 0x4937cc, /* rk5 */ 0x51380c, /* rk6 */
};

/* mov [g_active_inst], ecx ; inc dword [g_advance_count] ; jmp orig */
static void *make_advance_stub(void *orig)
{
    BYTE *s = (BYTE *)VirtualAlloc(NULL, 32, MEM_COMMIT | MEM_RESERVE,
                                   PAGE_EXECUTE_READWRITE);
    if (!s)
        return NULL;
    int p = 0;
    s[p++] = 0x89; s[p++] = 0x0D;
    *(DWORD *)(s + p) = (DWORD)(ULONG_PTR)&g_active_inst; p += 4;
    s[p++] = 0xFF; s[p++] = 0x05;
    *(DWORD *)(s + p) = (DWORD)(ULONG_PTR)&g_advance_count; p += 4;
    s[p++] = 0xE9;
    *(DWORD *)(s + p) = (DWORD)((BYTE *)orig - (s + p + 4)); p += 4;
    FlushInstructionCache(GetCurrentProcess(), s, p);
    return s;
}

static int install_advance_hooks(void)
{
    int n = 0;
    for (int i = 0; i < (int)(sizeof(GAME_VTABLE_RVAS) / sizeof(DWORD)); i++) {
        DWORD *vt = (DWORD *)(ULONG_PTR)(g_base + GAME_VTABLE_RVAS[i]);
        DWORD orig = vt[5];
        if (orig < g_base || orig > g_base + 0x600000) {
            log_line("[mmlc] advance hook: vtable %#lx slot5 %08lx bad",
                     (unsigned long)GAME_VTABLE_RVAS[i], (unsigned long)orig);
            continue;
        }
        void *stub = make_advance_stub((void *)(ULONG_PTR)orig);
        if (!stub)
            continue;
        DWORD old;
        if (!VirtualProtect(&vt[5], 4, PAGE_EXECUTE_READWRITE, &old))
            continue;
        vt[5] = (DWORD)(ULONG_PTR)stub;
        VirtualProtect(&vt[5], 4, old, &old);
        FlushInstructionCache(GetCurrentProcess(), &vt[5], 4);
        n++;
    }
    log_line("[mmlc] advance hooks installed on %d game vtable(s)", n);
    return n;
}

/* Skip the boot splash. The splash state machine (VA 0x43c2f0) waits for a
 * button in one state and fast-forwards when anyButtonPressed() (VA 0x43cc70)
 * returns true. That function has exactly one caller (the splash), so forcing
 * it to return 1 skips the logos without injecting synthetic controller state
 * (the earlier input-injection attempt corrupted the render path). */
#define INTRO_ANYBUTTON_RVA 0x3cc70UL

static int install_intro_skip(HMODULE mod)
{
    BYTE *fn = (BYTE *)mod + INTRO_ANYBUTTON_RVA;
    if (fn[0] != 0x56 || fn[1] != 0x57) {  /* push esi; push edi */
        log_line("[mmlc] intro skip: bad prologue at %p (%02x %02x)",
                 fn, fn[0], fn[1]);
        return -1;
    }
    DWORD old;
    if (!VirtualProtect(fn, 3, PAGE_EXECUTE_READWRITE, &old)) {
        log_line("[mmlc] intro skip: VirtualProtect failed (%lu)", GetLastError());
        return -1;
    }
    fn[0] = 0xB0;  /* mov al, 1 */
    fn[1] = 0x01;
    fn[2] = 0xC3;  /* ret */
    VirtualProtect(fn, 3, old, &old);
    FlushInstructionCache(GetCurrentProcess(), fn, 3);
    log_line("[mmlc] intro skip installed at %p", fn);
    return 0;
}

/* Stronger variant: point the MM1 vtable's slot 4 (per-cycle CPU step,
 * __thiscall + 1 stack arg) and slot 5 (advance) at no-op stubs. This stops
 * the engine's CPU regardless of how it is invoked. */
static void WINAPI mesen_slot4_stub(DWORD cycles) { (void)cycles; }
static void WINAPI mesen_advance_stub(void) { }
#define MM1_VTABLE_RVA 0x2d04b4UL

static int install_vtable_freeze(void)
{
    DWORD *vt = (DWORD *)(ULONG_PTR)(g_base + MM1_VTABLE_RVA);
    DWORD old;
    if (!VirtualProtect(&vt[4], 8, PAGE_EXECUTE_READWRITE, &old)) {
        log_line("[mmlc] vtable freeze: VirtualProtect failed (%lu)", GetLastError());
        return -1;
    }
    log_line("[mmlc] vtable freeze: slot4=%08lx slot5=%08lx", vt[4], vt[5]);
    vt[4] = (DWORD)(ULONG_PTR)mesen_slot4_stub;
    vt[5] = (DWORD)(ULONG_PTR)mesen_advance_stub;
    VirtualProtect(&vt[4], 8, old, &old);
    FlushInstructionCache(GetCurrentProcess(), &vt[4], 8);
    return 0;
}

/* --- Overclock (tie to MMLC's in-game "CPU SPEED" option) ------------------
 * MMLC's options store CPU SPEED (ORIGINAL/TURBO) as a float at Options2.sav
 * +0x28 (locale: "CPU SPEED" / "ORIGINAL" / "TURBO"). We mirror TURBO onto
 * Mesen's NES overclock (extra PPU scanlines, keeps 60 fps -> less slowdown).
 * `mesen_overclock` sets an explicit scanline count (disables following);
 * `mesen_overclock_follow` (default 1) follows the in-game option;
 * `mesen_overclock_turbo` (default 262) is the scanline count for TURBO. */
static int g_oc_follow = 1;
static int g_oc_manual = -1;     /* >=0: explicit scanlines, ignore in-game */
static int g_oc_turbo = 262;
static int g_oc_applied = -1;

static void mesen_apply_overclock(float speed)
{
    int sl;
    if (g_oc_manual >= 0)
        sl = g_oc_manual;
    else if (!g_oc_follow)
        return;
    else
        sl = (speed > 1.001f) ? g_oc_turbo : 0;
    if (sl < 0) sl = 0;
    if (sl > 8192) sl = 8192;
    if (sl != g_oc_applied) {
        g_oc_applied = sl;
        if (mesen_bridge_ready()) {
            mesen_bridge_set_overclock((uint32_t)sl);
            log_line("[mmlc] overclock: CPU SPEED=%.3f -> %d scanlines",
                     speed, sl);
        }
    }
}

/* Watch the MMLC options file and map CPU SPEED (ORIGINAL/TURBO) onto Mesen's
 * NES overclock. Logs the raw bytes whenever it changes. */
static DWORD WINAPI mesen_options_watch_thread(LPVOID p)
{
    (void)p;
    char appdata[MAX_PATH] = "";
    if (!GetEnvironmentVariableA("APPDATA", appdata, sizeof(appdata)))
        return 0;
    char path[MAX_PATH] = "";
    char glob[MAX_PATH];
    snprintf(glob, sizeof(glob), "%s\\MegaMan\\*", appdata);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(glob, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) &&
                fd.cFileName[0] != '.') {
                snprintf(path, sizeof(path), "%s\\MegaMan\\%s\\Options2.sav",
                         appdata, fd.cFileName);
                break;
            }
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    if (!path[0]) {
        log_line("[mmlc] options watch: no Options2.sav found");
        return 0;
    }
    log_line("[mmlc] options watch: %s", path);
    static BYTE last[256];
    DWORD lastlen = 0;
    for (;;) {
        FILE *f = fopen(path, "rb");
        if (f) {
            BYTE buf[256];
            size_t r = fread(buf, 1, sizeof(buf), f);
            fclose(f);
            if (r != lastlen || (r && memcmp(buf, last, r))) {
                char hex[3 * 256 + 1];
                int n = 0;
                for (size_t k = 0; k < r && n < (int)sizeof(hex) - 3; k++)
                    n += snprintf(hex + n, sizeof(hex) - n, "%02x", buf[k]);
                log_line("[mmlc] Options2.sav (%zu): %s", r, hex);
                if (r >= 0x30) {
                    float spd;
                    memcpy(&spd, buf + 0x28, 4);
                    log_line("[mmlc] options fields: f0x28=%g u0x24=%u u0x2c=%u "
                             "u0x30=%u", spd, *(uint32_t *)(buf + 0x24),
                             *(uint32_t *)(buf + 0x2c), *(uint32_t *)(buf + 0x30));
                    mesen_apply_overclock(spd);
                }
                memcpy(last, buf, r);
                lastlen = (DWORD)r;
            }
        }
        Sleep(500);
    }
    return 0;
}

/* Keep Mesen in sync with the engine's pause (e.g. the MMLC in-game menu
 * pauses the engine's emulation; pause Mesen too so audio/video stay aligned).
 * Detected by watching the NESSystem cycle counter (+0x50D78) stall. */
static DWORD WINAPI mesen_sync_pause_thread(LPVOID p)
{
    (void)p;
    int paused = 0, stuck = 0;
    DWORD last_cyc = 0;
    for (int i = 0; i < 7200; i++) {
        DWORD inst = g_nes_inst;
        if (inst) {
            DWORD cyc = *(volatile DWORD *)(ULONG_PTR)(inst + 0x50D78);
            if (cyc == last_cyc) {
                if (++stuck >= 4 && !paused && mesen_bridge_pause() == 0) {
                    paused = 1;
                    log_line("[mmlc] engine paused -> Mesen paused");
                }
            } else {
                stuck = 0;
                if (paused && mesen_bridge_resume() == 0) {
                    paused = 0;
                    log_line("[mmlc] engine resumed -> Mesen resumed");
                }
            }
            last_cyc = cyc;
            if (i % 12 == 0) {
                static int _synclog = 0;
                if (_synclog < 24) {
                    _synclog++;
                    log_line("[mmlc] sync: cyc=%lu stuck=%d paused=%d",
                             cyc, stuck, paused);
                }
            }
        }
        Sleep(250);
    }
    return 0;
}

/* Diagnostic: is the engine's CPU still advancing? (cycle counter at +0x50D78) */
/* Pause Mesen whenever the engine stops advancing (the MMLC in-game UI pauses
 * the emulation). The `advance` hook increments g_advance_count each step. */
static DWORD WINAPI mesen_ui_pause_thread(LPVOID p)
{
    (void)p;
    DWORD last = 0;
    int stuck = 0;
    for (;;) {
        Sleep(250);
        if (!mesen_bridge_ready())
            continue;
        DWORD c = g_advance_count;
        if (c != last) {
            stuck = 0;
            if (g_ui_paused) {
                mesen_bridge_resume();
                g_ui_paused = 0;
                log_line("[mmlc] engine running -> Mesen resumed");
            }
        } else if (++stuck >= 2 && !g_ui_paused && g_loaded_ridx >= 0) {
            mesen_bridge_pause();
            g_ui_paused = 1;
            log_line("[mmlc] engine paused -> Mesen paused");
        }
        last = c;
    }
    return 0;
}

static DWORD WINAPI mesen_engine_probe_thread(LPVOID p)
{
    (void)p;
    for (int i = 0; i < 20; i++) {
        DWORD inst = g_probe_this;
        if (inst) {
            DWORD cyc = *(volatile DWORD *)(ULONG_PTR)(inst + 0x50D78);
            log_line("[mmlc] engine cycle=%lu frozen=%d", cyc, g_mesen_freeze);
        }
        Sleep(1000);
    }
    return 0;
}

/* Patch Map/Unmap/UpdateSubresource in the immediate context's vtable. */
static int install_d3d_context_hook(DWORD ctx, DWORD dev)
{
    DWORD *vt = *(DWORD **)(ULONG_PTR)ctx;
    if (!vt) {
        log_line("[mmlc] d3d hook: context %08lx has no vtable", ctx);
        return -1;
    }
    DWORD map = vt[0x38 / 4], unm = vt[0x3c / 4];
    DWORD copysub = vt[0xb8 / 4], copyres = vt[0xbc / 4];
    DWORD updsub = vt[0xc0 / 4];
    log_line("[mmlc] d3d hook: ctx=%08lx dev=%08lx vt=%08lx map=%08lx(%s) "
             "unmap=%08lx(%s) updsub=%08lx(%s)", ctx, dev, (DWORD)(ULONG_PTR)vt,
             map, ptr_in_module(map, "d3d11.dll") ? "d3d11" : "?",
             unm, ptr_in_module(unm, "d3d11.dll") ? "d3d11" : "?",
             updsub, ptr_in_module(updsub, "d3d11.dll") ? "d3d11" : "?");
    if (!ptr_in_module(map, "d3d11.dll") || !ptr_in_module(unm, "d3d11.dll")) {
        log_line("[mmlc] d3d hook: vtable entries not in d3d11.dll (not installed)");
        return -1;
    }
    DWORD old;
    /* 0x38 .. 0xc0 inclusive = 0x8c bytes. */
    if (!VirtualProtect(&vt[0x38 / 4], 0x8c, PAGE_EXECUTE_READWRITE, &old)) {
        log_line("[mmlc] d3d hook: VirtualProtect failed (%lu)", GetLastError());
        return -1;
    }
    g_real_d3d_map = (mesen_map_fn)map;
    g_real_d3d_unmap = (mesen_unmap_fn)unm;
    vt[0x38 / 4] = (DWORD)(ULONG_PTR)hook_d3d_map;
    vt[0x3c / 4] = (DWORD)(ULONG_PTR)hook_d3d_unmap;
    if (ptr_in_module(updsub, "d3d11.dll")) {
        g_real_d3d_updsub = (mesen_updsub_fn)updsub;
        vt[0xc0 / 4] = (DWORD)(ULONG_PTR)hook_d3d_update_subresource;
    }
    if (ptr_in_module(copysub, "d3d11.dll")) {
        g_real_d3d_copysub = (mesen_copysub_fn)copysub;
        vt[0xb8 / 4] = (DWORD)(ULONG_PTR)hook_d3d_copy_subresource_region;
    }
    if (ptr_in_module(copyres, "d3d11.dll")) {
        g_real_d3d_copyres = (mesen_copyres_fn)copyres;
        vt[0xbc / 4] = (DWORD)(ULONG_PTR)hook_d3d_copy_resource;
    }
    VirtualProtect(&vt[0x38 / 4], 0x8c, old, &old);
    FlushInstructionCache(GetCurrentProcess(), &vt[0x38 / 4], 0x8c);
    log_line("[mmlc] d3d Map/Unmap/UpdateSubresource/Copy hook installed");
    return 0;
}

/* Wait for the renderer singleton (created lazily on first render init). */
static DWORD WINAPI mesen_d3d_watch_thread(LPVOID p)
{
    (void)p;
    for (int i = 0; i < 900; i++) {
        DWORD obj = *(volatile DWORD *)(ULONG_PTR)(g_base + MESEN_RENDERER_RVA);
        if (obj) {
            DWORD dev = *(DWORD *)(ULONG_PTR)(obj + 0x60);
            DWORD ctx = *(DWORD *)(ULONG_PTR)(obj + 0x64);
            log_line("[mmlc] d3d hook: renderer obj=%08lx dev=%08lx ctx=%08lx",
                     obj, dev, ctx);
            if (dev)
                install_d3d_device_hook(dev);
            if (ctx)
                install_d3d_context_hook(ctx, dev);
            return 0;
        }
        Sleep(100);
    }
    log_line("[mmlc] d3d hook: renderer singleton never appeared");
    return 0;
}
/* --- Mesen ROM handoff (all games) ---------------------------------------
 * 20xx extracts the legal ROMs to roms/<key>.nes; on each game's ctor we queue
 * that ROM for the host and silence the engine with an idle ROM. The load is
 * released when the engine's 4x NES screen first appears (a game actually
 * started), see mesen_note_game_screen(). */
static BYTE g_mesen_prg[0x100000];
static BYTE g_mesen_chr[0x100000];
static DWORD g_mesen_prg_size = 0;
static DWORD g_mesen_chr_size = 0;
static DWORD g_mesen_mapper = 0;
static DWORD g_mesen_mirror = 0;
static char g_mesen_key[16] = { 0 };
static int g_mesen_null_rom = 1;  /* give the engine a harmless idle ROM */

static void log_line(const char *fmt, ...)
{
    if (!g_log[0])
        return;
    FILE *f = fopen(g_log, "a");
    if (!f)
        return;
    va_list ap;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

/* read a simple key=value ini; returns 1 if key present */
static int cfg_get(const char *path, const char *key, char *out, size_t outsz)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return 0;
    char line[512];
    size_t klen = strlen(key);
    int found = 0;
    while (fgets(line, sizeof(line), f)) {
        char *p = line;
        while (*p == ' ' || *p == '\t')
            p++;
        if (*p == '#' || *p == ';')
            continue;
        if (_strnicmp(p, key, klen) == 0) {
            /* Exact key: the name must be followed by optional space then '='
             * (otherwise "mesen" would match "mesen_probe"). */
            char *q = p + klen;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != '=')
                continue;
            char *eq = q;
            char *v = eq + 1;
            while (*v == ' ' || *v == '\t')
                v++;
            char *end = v + strlen(v);
            while (end > v && (end[-1] == '\n' || end[-1] == '\r' ||
                               end[-1] == ' ' || end[-1] == '\t'))
                *--end = 0;
            strncpy(out, v, outsz - 1);
            out[outsz - 1] = 0;
            found = 1;
            break;
        }
    }
    fclose(f);
    return found;
}

static int cfg_flag(const char *path, const char *key, int def)
{
    char v[32];
    if (!cfg_get(path, key, v, sizeof(v)))
        return def;
    if (!_stricmp(v, "1") || !_stricmp(v, "true") || !_stricmp(v, "yes") ||
        !_stricmp(v, "on"))
        return 1;
    if (!_stricmp(v, "0") || !_stricmp(v, "false") || !_stricmp(v, "no") ||
        !_stricmp(v, "off"))
        return 0;
    return def;
}

/* ------------------------------------------------------------------ *
 * AOB (array-of-bytes) scanner with '??' wildcards.
 * ------------------------------------------------------------------ */

#define AOB_MAX 64

static int hexval(int c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

/* parse "66 8B ?? DF" -> bytes[]/mask[]; returns length or -1. */
static int aob_parse(const char *s, unsigned char *bytes, unsigned char *mask)
{
    int n = 0;
    while (*s) {
        while (*s == ' ' || *s == '\t')
            s++;
        if (!*s)
            break;
        if (n >= AOB_MAX)
            return -1;
        if (s[0] == '?' && s[1] == '?') {
            bytes[n] = 0;
            mask[n] = 0;
            s += 2;
        } else {
            int hi = hexval(s[0]), lo = hexval(s[1]);
            if (hi < 0 || lo < 0)
                return -1;
            bytes[n] = (unsigned char)((hi << 4) | lo);
            mask[n] = 0xFF;
            s += 2;
        }
        n++;
    }
    return n;
}

static int aob_match(const unsigned char *p, const unsigned char *b,
                     const unsigned char *m, int len)
{
    for (int i = 0; i < len; i++)
        if (m[i] && p[i] != b[i])
            return 0;
    return 1;
}

/* Find up to max hits of an AOB across every section of a module.
 * Returns the number written to out[]. */
static int aob_scan(HMODULE mod, const char *sig, void **out, int max)
{
    unsigned char bytes[AOB_MAX], mask[AOB_MAX];
    int len = aob_parse(sig, bytes, mask);
    if (len <= 0)
        return -1;

    BYTE *base = (BYTE *)mod;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE)
        return -1;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE)
        return -1;

    int hits = 0;
    IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
    for (WORD i = 0; i < nt->FileHeader.NumberOfSections && hits < max; i++, sec++) {
        SIZE_T span = sec->Misc.VirtualSize ? sec->Misc.VirtualSize
                                            : sec->SizeOfRawData;
        BYTE *start = base + sec->VirtualAddress;
        if (!(sec->Characteristics & IMAGE_SCN_MEM_EXECUTE))
            continue;
        for (SIZE_T j = 0; j + (SIZE_T)len <= span && hits < max; j++) {
            if (aob_match(start + j, bytes, mask, len))
                out[hits++] = start + j;
        }
    }
    return hits;
}

/* Log every AOB hit for the NESSystem virtuals and cross-check the vtable. */
static void scan_virtuals(HMODULE mod)
{
    static const struct {
        const char *name;
        const char *sig;
    } targets[] = {
        { "bank_xlate", SIG_BANKXLATE },
        { "read6502", SIG_READ6502 },
        { "advance", SIG_ADVANCE },
    };
    void *hits[4];
    DWORD base = (DWORD)(ULONG_PTR)mod;

    for (int t = 0; t < (int)(sizeof(targets) / sizeof(targets[0])); t++) {
        int n = aob_scan(mod, targets[t].sig, hits, 4);
        if (n <= 0) {
            log_line("[mmlc] %-10s : no match", targets[t].name);
            continue;
        }
        for (int i = 0; i < n; i++)
            log_line("[mmlc] %-10s : VA %p (base+%#lx)",
                     targets[t].name, hits[i],
                     (unsigned long)((DWORD)(ULONG_PTR)hits[i] - base));
    }
}

/* ------------------------------------------------------------------ *
 * Runtime ROM injection.
 *
 * FUN_0045c220 is called by each game ctor once per patch site, right
 * after memcpy(heap, &ROM_src, prg_size). We replace it entirely with a
 * handler that, on the first call for an instance, copies a same-size ROM
 * from rom_dir/<key>.bin over both the .rdata source (this+0x810) and the
 * heap copy (this+0x81C).
 * ------------------------------------------------------------------ */

static const rom_src_t *rom_for(unsigned long src_rva)
{
    for (int i = 0; i < (int)(sizeof(ROM_SRCS) / sizeof(ROM_SRCS[0])); i++)
        if (ROM_SRCS[i].src_rva == src_rva)
            return &ROM_SRCS[i];
    return NULL;
}

void inject_for_instance(void *thisptr, unsigned long offset);
extern void overlay_hook(void);  /* src/proxy/overlay_hook.S */
extern void hook_asset(void);    /* src/proxy/overlay_hook.S */

static unsigned long crc32_of(const BYTE *p, size_t n)
{
    unsigned long c = 0xffffffffUL;
    for (size_t i = 0; i < n; i++) {
        c ^= p[i];
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320UL & (unsigned long)(-(long)(c & 1)));
    }
    return ~c & 0xffffffffUL;
}

static void write_rom(DWORD src, DWORD heap, const BYTE *data, DWORD size)
{
    DWORD old;
    if (VirtualProtect((void *)src, size, PAGE_EXECUTE_READWRITE, &old)) {
        memcpy((void *)src, data, size);
        VirtualProtect((void *)src, size, old, &old);
    }
    if (VirtualProtect((void *)heap, size, PAGE_READWRITE, &old)) {
        memcpy((void *)heap, data, size);
        VirtualProtect((void *)heap, size, old, &old);
    }
}

/* A minimal idle ROM for any PRG size: NOP sled, JMP-to-self at $FFF0, and
 * all vectors pointing there. Neutralizes the engine's own emulation while
 * Mesen runs the real game. */
static void make_null_rom(BYTE *prg, DWORD size)
{
    if (size < 0x20)
        return;
    memset(prg, 0xEA, size);                     /* NOP sled */
    prg[size - 0x10] = 0x4C;                     /* JMP $FFF0 */
    prg[size - 0x0F] = 0xF0;
    prg[size - 0x0E] = 0xFF;
    prg[size - 6] = 0xF0; prg[size - 5] = 0xFF;  /* NMI vector */
    prg[size - 4] = 0xF0; prg[size - 3] = 0xFF;  /* reset vector */
    prg[size - 2] = 0xF0; prg[size - 1] = 0xFF;  /* IRQ vector */
}

/* Read roms/<key>.nes (written by 20xx) into the pending Mesen buffers.
 * Falls back to a headerless roms/<key>.bin. Returns 1 on success. */
static int mesen_read_game_rom(const rom_src_t *rom)
{
    char path[MAX_PATH];
    static BYTE file[0x100000];

    snprintf(path, sizeof(path), "%s%s.nes", g_romdir, rom->key);
    FILE *f = fopen(path, "rb");
    if (f) {
        size_t n = fread(file, 1, sizeof(file), f);
        fclose(f);
        if (n >= 16 && file[0] == 'N' && file[1] == 'E' &&
            file[2] == 'S' && file[3] == 0x1A) {
            DWORD prg = (DWORD)file[4] * 0x4000;
            DWORD chr = (DWORD)file[5] * 0x2000;
            if (16 + prg + chr <= n && prg && prg <= sizeof(g_mesen_prg)) {
                memcpy(g_mesen_prg, file + 16, prg);
                if (chr) memcpy(g_mesen_chr, file + 16 + prg, chr);
                g_mesen_prg_size = prg;
                g_mesen_chr_size = chr;
                g_mesen_mapper = (file[6] >> 4) | (file[7] & 0xF0);
                g_mesen_mirror = file[6] & 1;
                return 1;
            }
        }
        log_line("[mmlc] mesen: bad .nes header for %s", rom->key);
    }

    snprintf(path, sizeof(path), "%s%s.bin", g_romdir, rom->key);
    f = fopen(path, "rb");
    if (f) {
        size_t n = fread(file, 1, sizeof(file), f);
        fclose(f);
        if (n >= 16 && file[0] == 'N' && file[1] == 'E' &&
            file[2] == 'S' && file[3] == 0x1A) {
            DWORD prg = (DWORD)file[4] * 0x4000;
            DWORD chr = (DWORD)file[5] * 0x2000;
            if (16 + prg + chr <= n) {
                memcpy(g_mesen_prg, file + 16, prg);
                if (chr) memcpy(g_mesen_chr, file + 16 + prg, chr);
                g_mesen_prg_size = prg;
                g_mesen_chr_size = chr;
                g_mesen_mapper = (file[6] >> 4) | (file[7] & 0xF0);
                g_mesen_mirror = file[6] & 1;
                return 1;
            }
        } else if (n == rom->prg_size) {
            memcpy(g_mesen_prg, file, n);
            g_mesen_prg_size = (DWORD)n;
            g_mesen_chr_size = 0;
            g_mesen_mapper = rom->mapper;
            g_mesen_mirror = rom->mirroring;
            return 1;
        }
    }
    return 0;
}

/* Prepare Mesen for a game: read its .nes and silence the engine with an idle
 * ROM. The host load is queued until the game screen appears. */
static void mesen_prepare_game(const rom_src_t *rom, int ridx, DWORD src, DWORD heap)
{
    if (g_mesen_loaded[ridx])
        return;
    if (!mesen_read_game_rom(rom)) {
        log_line("[mmlc] mesen: no roms/%s.nes (run: 20xx extract); "
                 "engine ROM left intact", rom->key);
        return;
    }
    static BYTE null_rom[0x100000];
    DWORD engine_size = rom->prg_size;
    int nulled = 0;
    if (g_mesen_null_rom && mesen_bridge_ready()) {
        make_null_rom(null_rom, engine_size);
        write_rom(src, heap, null_rom, engine_size);
        FlushInstructionCache(GetCurrentProcess(), (void *)heap, engine_size);
        nulled = 1;
    }
    g_mesen_loaded[ridx] = 1;
    log_line("[mmlc] mesen: %s prepared prg=%#lx chr=%#lx mapper=%lu mirror=%lu "
             "null=%d connected=%d",
             rom->key, (unsigned long)g_mesen_prg_size,
             (unsigned long)g_mesen_chr_size, (unsigned long)g_mesen_mapper,
             (unsigned long)g_mesen_mirror, nulled, mesen_bridge_ready());
}

/* Map a stepped NESSystem instance to its ROM_SRCS index. */
static int inst_ridx(DWORD inst)
{
    for (int i = 0; i < g_npatched; i++)
        if (g_patched_inst[i] == inst)
            return g_inst_ridx[i];
    return -1;
}

/* Load the ROM of the instance the front-end is currently stepping. Called each
 * frame the 4x NES screen is mapped, so Mesen follows the game-select carousel.
 * Returns the active ROM_SRCS index (or -1). */
static int mesen_active_ridx(void)
{
    if (!g_active_inst)
        return -1;
    return inst_ridx(g_active_inst);
}

static void mesen_load_active(void)
{
    if (!g_mesen || !mesen_bridge_ready())
        return;
    int ridx = mesen_active_ridx();
    if (ridx < 0 || ridx == g_loaded_ridx)
        return;
    const rom_src_t *rom = &ROM_SRCS[ridx];
    if (!mesen_read_game_rom(rom))
        return;
    if (mesen_bridge_load_rom(g_mesen_prg, g_mesen_prg_size,
                              g_mesen_chr_size ? g_mesen_chr : NULL,
                              g_mesen_chr_size,
                              g_mesen_mapper, g_mesen_mirror) == 0) {
        g_loaded_ridx = ridx;
        log_line("[mmlc] mesen: loaded %s (active) prg=%#lx chr=%#lx crc=%08lx",
                 rom->key, (unsigned long)g_mesen_prg_size,
                 (unsigned long)g_mesen_chr_size,
                 crc32_of(g_mesen_prg, g_mesen_prg_size));
    } else {
        log_line("[mmlc] mesen: load %s failed", rom->key);
    }
}

static DWORD WINAPI mesen_probe_thread(LPVOID p)
{
    (void)p;
    /* Wait until the emulator is actually running (cycle counter != 0), i.e.
     * the player started a game; a preloaded-but-idle instance is static. */
    int waited = 0;
    while (waited < 1200 && g_probe_this &&
           *(volatile DWORD *)(ULONG_PTR)(g_probe_this + 0x50D78) == 0) {
        Sleep(100);
        waited++;
    }
    if (!g_probe_this) {
        log_line("[mmlc] probe: no instance captured");
        return 0;
    }
    log_line("[mmlc] probe: emulator active after %d ms", waited * 100);
    /* Several snapshots ~0.7 s apart: diffing consecutive dumps isolates the
     * per-frame framebuffer (static ROM/state cancels out). */
    for (int i = 0; i < 4; i++) {
        char path[MAX_PATH];
        snprintf(path, sizeof(path), "%smmlc_mesen_dump%d.bin", g_dir, i);
        FILE *f = fopen(path, "wb");
        if (f) {
            fwrite((void *)(ULONG_PTR)g_probe_this, 1, PROBE_DUMP_SIZE, f);
            fclose(f);
            log_line("[mmlc] probe: dump %d this=%08lx -> %s",
                     i, g_probe_this, path);
        }
        Sleep(700);
    }
    return 0;
}

void inject_for_instance(void *thisptr, unsigned long offset)
{
    (void)offset;
    BYTE *self = (BYTE *)thisptr;
    DWORD src = *(DWORD *)(self + 0x810);
    DWORD heap = *(DWORD *)(self + 0x81C);

    if (!src || !heap)
        return;

    g_nes_inst = (DWORD)(ULONG_PTR)thisptr;  /* for the RAM-sync experiment */

    for (int i = 0; i < g_npatched; i++)
        if (g_patched_inst[i] == (DWORD)(ULONG_PTR)thisptr)
            return;
    if (g_npatched < (int)(sizeof(g_patched_inst) / sizeof(g_patched_inst[0])))
        g_patched_inst[g_npatched++] = (DWORD)(ULONG_PTR)thisptr;

    const rom_src_t *rom = rom_for(src - g_base);
    if (!rom) {
        log_line("[mmlc] inject: unknown ROM source rva %#lx",
                 (unsigned long)(src - g_base));
        return;
    }
    const char *key = rom->key;
    DWORD prg_size = rom->prg_size;
    static BYTE buf[0x100000];
    int ridx = (int)(rom - ROM_SRCS);

    /* Remember which game this instance is, so the `advance` hook can tell
     * which game the front-end is previewing/playing. */
    if (g_npatched > 0)
        g_inst_ridx[g_npatched - 1] = ridx;

    /* Option-2 probe: capture the instance and dump it at the title screen. */
    if (g_mesen_probe && !g_probe_this) {
        g_probe_this = (DWORD)(ULONG_PTR)thisptr;
        if (InterlockedExchange(&g_probe_started, 1) == 0) {
            HANDLE h = CreateThread(NULL, 0, mesen_probe_thread, NULL, 0, NULL);
            if (h)
                CloseHandle(h);
        }
    }

    /* Mesen mode: hand the game's .nes to the host and silence the engine. */
    if (g_mesen) {
        mesen_prepare_game(rom, ridx, src, heap);
        log_line("[mmlc] inject: %s this=%p src=%08lx heap=%08lx (mesen)",
                 key, thisptr, (unsigned long)src, (unsigned long)heap);
        return;
    }

    if (!g_rom_inject)
        return;

    char path[MAX_PATH];
    snprintf(path, sizeof(path), "%s%s.bin", g_romdir, key);
    FILE *f = fopen(path, "rb");
    if (!f) {
        log_line("[mmlc] inject: no %s (skipped)", path);
        return;
    }
    size_t n = fread(buf, 1, sizeof(buf), f);
    fclose(f);

    BYTE *data = buf;
    if (n >= 16 && buf[0] == 'N' && buf[1] == 'E' && buf[2] == 'S' && buf[3] == 0x1A) {
        data += 16;  /* strip iNES header */
        n -= 16;
    }
    if (n != prg_size) {
        log_line("[mmlc] inject: %s size %#zx != PRG %#lx (skipped)",
                 key, n, (unsigned long)prg_size);
        return;
    }

    write_rom(src, heap, data, prg_size);
    log_line("[mmlc] inject: %s %#lx bytes crc %08lx (src=%p heap=%p)",
             key, (unsigned long)prg_size, crc32_of(data, prg_size),
             (void *)src, (void *)heap);
}

static int install_overlay_hook(HMODULE mod)
{
    void *hits[4];
    int n = aob_scan(mod, SIG_OVERLAY_FN, hits, 4);
    if (n != 1) {
        log_line("[mmlc] overlay hook: %d match(es) (not installed)", n);
        return -1;
    }
    BYTE *fn = (BYTE *)hits[0];
    DWORD old;
    if (!VirtualProtect(fn, 5, PAGE_EXECUTE_READWRITE, &old)) {
        log_line("[mmlc] overlay hook: VirtualProtect failed (%lu)", GetLastError());
        return -1;
    }
    DWORD rel = (DWORD)((BYTE *)overlay_hook - (fn + 5));
    fn[0] = 0xE9;
    memcpy(fn + 1, &rel, 4);
    VirtualProtect(fn, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), fn, 5);
    log_line("[mmlc] overlay hook installed at %p", fn);
    return 0;
}

static int g_back_prev = 0;
static int g_mesen_paused = 0;
static int16_t g_acc[32768];     /* audio accumulation buffer */
static int g_acc_n = 0;
static int g_skip_intro = 1;     /* skip the boot splash (see install_intro_skip) */

/* Called by the asset hook stub with the requested asset name. */
void log_asset(const char *name)
{
    if (!name)
        return;
    /* The wrapper resumes by loading a save state; reset Mesen so it starts
     * from the title instead of inheriting the forwarded menu inputs. */
    if (g_mesen && (strstr(name, "savestate") || strstr(name, "Savestate") ||
                    strstr(name, ".sav"))) {
        log_line("[mmlc] savestate asset: %s -> Mesen reset", name);
        mesen_bridge_reset();
    }
    if (g_asset_log && (strstr(name, "Museum") || strstr(name, "museum") ||
                        strstr(name, "Database") || strstr(name, "prod")))
        log_line("[mmlc] asset: %s", name);
}

static int install_asset_hook(HMODULE mod)
{
    void *hits[2];
    int n = aob_scan(mod, SIG_ASSET, hits, 2);
    if (n != 1) {
        log_line("[mmlc] asset hook: %d match(es) (not installed)", n);
        return -1;
    }
    BYTE *fn = (BYTE *)hits[0];
    const int stolen = 5;
    BYTE *tr = (BYTE *)VirtualAlloc(NULL, stolen + 5,
                                    MEM_COMMIT | MEM_RESERVE,
                                    PAGE_EXECUTE_READWRITE);
    if (!tr) {
        log_line("[mmlc] asset hook: VirtualAlloc failed (%lu)", GetLastError());
        return -1;
    }
    memcpy(tr, fn, stolen);
    tr[stolen] = 0xE9;
    DWORD back = (DWORD)((fn + stolen) - (tr + stolen + 5));
    memcpy(tr + stolen + 1, &back, 4);
    g_tramp_asset = tr;

    DWORD old;
    if (!VirtualProtect(fn, stolen, PAGE_EXECUTE_READWRITE, &old))
        return -1;
    fn[0] = 0xE9;
    DWORD rel = (DWORD)((BYTE *)hook_asset - (fn + 5));
    memcpy(fn + 1, &rel, 4);
    VirtualProtect(fn, stolen, old, &old);
    FlushInstructionCache(GetCurrentProcess(), fn, stolen);
    log_line("[mmlc] asset hook installed at %p tramp=%p", fn, tr);
    return 0;
}

/* ------------------------------------------------------------------ *
 * Controller input: MMLC reads only XInput (XInputGetState, ordinal 2 of
 * XINPUT1_3). Hook that IAT entry so ANY connected controller drives the
 * game: if the queried user index is disconnected, fall back to the first
 * connected one. (Also the place to overlay injected inputs for testing.)
 * ------------------------------------------------------------------ */
typedef DWORD (WINAPI *XInputGetState_t)(DWORD, void *);
static XInputGetState_t g_real_xig = NULL;
static int g_xinput_hook = 0;
static int g_all_controllers = 0;  /* ini all_controllers */
static int g_menu_rb = 0;          /* ini menu_rb: open the MMLC UI with RB */
/* XInput button that opens the MMLC in-game menu; configurable via
 * `menu_button` in mmlc.ini (name or hex mask). Default: Back (View). */
static DWORD g_menu_btn = 0x0020;

/* XInput -> NES button mapping, configurable via mmlc.ini (btn_a..btn_right).
 * Defaults match the collection's layout (NES B = Xbox X). */
static DWORD g_btn_a = 0x1000, g_btn_b = 0x4000, g_btn_select = 0x0200,
             g_btn_start = 0x0010, g_btn_up = 0x0001, g_btn_down = 0x0002,
             g_btn_left = 0x0004, g_btn_right = 0x0008;

/* Mesen hotkey combos. Defaults: LB+Y save, LB+B load, LB+X reset (soft reset;
 * Mesen auto-loads its state on reset). LB/Y/B/X are unused by the default NES
 * mapping, so the combos are pure hotkeys. Configurable via
 * `state_save_button` / `state_load_button` / `state_reset_button`
 * (e.g. "lb+y"); `state_slot` picks the save slot. */
static DWORD g_state_save_btn = 0x8100;  /* LB(0x100) | Y(0x8000) */
static DWORD g_state_load_btn = 0x2100;  /* LB(0x100) | B(0x2000) */
static DWORD g_state_reset_btn = 0x4100; /* LB(0x100) | X(0x4000) */
static int g_state_slot = 0;
static DWORD g_state_prev = 0;

static DWORD parse_button(const char *s)
{
    if (!s || !s[0])
        return 0;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X'))
        return strtoul(s, NULL, 16);
    if (!_stricmp(s, "a")) return 0x1000;
    if (!_stricmp(s, "b")) return 0x2000;
    if (!_stricmp(s, "x")) return 0x4000;
    if (!_stricmp(s, "y")) return 0x8000;
    if (!_stricmp(s, "lb")) return 0x0100;
    if (!_stricmp(s, "rb")) return 0x0200;
    if (!_stricmp(s, "back") || !_stricmp(s, "view")) return 0x0020;
    if (!_stricmp(s, "start") || !_stricmp(s, "menu")) return 0x0010;
    if (!_stricmp(s, "ls") || !_stricmp(s, "lthumb")) return 0x0040;
    if (!_stricmp(s, "rs") || !_stricmp(s, "rthumb")) return 0x0080;
    if (!_stricmp(s, "guide")) return 0x0400;
    if (!_stricmp(s, "dpad_up")) return 0x0001;
    if (!_stricmp(s, "dpad_down")) return 0x0002;
    if (!_stricmp(s, "dpad_left")) return 0x0004;
    if (!_stricmp(s, "dpad_right")) return 0x0008;
    return strtoul(s, NULL, 0);
}

/* Parse a '+'-/','-separated button combo (e.g. "lb+y") into a mask. */
static DWORD parse_button_combo(const char *s)
{
    char buf[64];
    DWORD mask = 0;
    if (!s || !s[0])
        return 0;
    snprintf(buf, sizeof(buf), "%s", s);
    for (char *tok = strtok(buf, "+,"); tok; tok = strtok(NULL, "+,"))
        mask |= parse_button(tok);
    return mask;
}

static int g_xig_logs = 0;
/* XInput wButtons -> NES $4016 bits (A=1,B=2,Select=4,Start=8,Up=16,Down=32,
 * Left=64,Right=128). */
static uint32_t xinput_to_nes(unsigned int w)
{
    uint32_t b = 0;
    if (w & g_btn_a) b |= 0x01;       /* A */
    if (w & g_btn_b) b |= 0x02;       /* B */
    if (w & g_btn_select) b |= 0x04;  /* Select */
    if (w & g_btn_start) b |= 0x08;   /* Start */
    if (w & g_btn_up) b |= 0x10;      /* Up */
    if (w & g_btn_down) b |= 0x20;    /* Down */
    if (w & g_btn_left) b |= 0x40;    /* Left */
    if (w & g_btn_right) b |= 0x80;   /* Right */
    return b;
}

static uint32_t g_last_nes_btn = 0xFFFFFFFFu;
static DWORD g_last_nes_tick = 0;

static DWORD WINAPI hook_XInputGetState(DWORD idx, void *state)
{
    DWORD r = g_real_xig ? g_real_xig(idx, state) : ERROR_DEVICE_NOT_CONNECTED;
    int fb = -1;
    if (r != ERROR_SUCCESS && g_all_controllers) {
        /* Fall back to any connected controller so all controllers work. */
        for (DWORD j = 0; j < 4; j++) {
            if (j == idx)
                continue;
            if (g_real_xig && g_real_xig(j, state) == ERROR_SUCCESS) {
                r = ERROR_SUCCESS;
                fb = (int)j;
                break;
            }
        }
    }
    /* MMLC in-game menu button (Back): pause Mesen immediately. The engine only
     * stops advancing a moment later, so waiting for that lets the player get
     * hit during the gap. Resume is handled when advance resumes. */
    if (idx == 0) {
        int back = (r == ERROR_SUCCESS && state) ?
                   ((*(unsigned short *)((char *)state + 4) & g_menu_btn) != 0) : 0;
        if (back && !g_back_prev && mesen_bridge_ready()) {
            mesen_bridge_pause();
            g_ui_paused = 1;
            log_line("[mmlc] menu button -> Mesen paused");
        }
        g_back_prev = back;
    }
    /* Remap the MMLC UI button to RB (right shoulder) for the engine, so the
     * wrapper menu no longer shares the NES Back/Select button. */
    if (g_menu_rb && r == ERROR_SUCCESS && state) {
        unsigned short *w = (unsigned short *)((char *)state + 4);
        if (*w & 0x0200)   /* XINPUT_GAMEPAD_RIGHT_SHOULDER */
            *w |= 0x0020;  /* -> XINPUT_GAMEPAD_BACK */
        *w &= (unsigned short)~0x0020;  /* suppress the real Back button */
    }
    /* Mesen pause follows the engine automatically (see mesen_ui_pause_thread):
     * when the MMLC in-game UI pauses the engine's `advance`, Mesen pauses too. */
    /* Forward the physical controller to the Mesen host (Phase 6). The host's
     * override persists, so only send on change (avoids blocking the input
     * path with a TCP round trip every poll). */
    if (g_mesen && mesen_bridge_ready()) {
        unsigned int btn = (r == ERROR_SUCCESS && state) ?
                           *(unsigned short *)((char *)state + 4) : 0;
        uint32_t nes = xinput_to_nes(btn);
        if (nes != g_last_nes_btn) {
            g_last_nes_btn = nes;
            g_last_nes_tick = GetTickCount();
            mesen_bridge_set_input(0, nes);
        }
        /* Save/load/reset hotkeys (rising edge on the combo). */
        if (r == ERROR_SUCCESS && state) {
            if (g_state_save_btn &&
                (btn & g_state_save_btn) == g_state_save_btn) {
                if ((g_state_prev & g_state_save_btn) != g_state_save_btn &&
                    mesen_bridge_save_state((uint32_t)g_state_slot) == 0)
                    log_line("[mmlc] state saved (slot %d)", g_state_slot);
            } else if (g_state_load_btn &&
                       (btn & g_state_load_btn) == g_state_load_btn) {
                if ((g_state_prev & g_state_load_btn) != g_state_load_btn &&
                    mesen_bridge_load_state((uint32_t)g_state_slot) == 0)
                    log_line("[mmlc] state loaded (slot %d)", g_state_slot);
            } else if (g_state_reset_btn &&
                       (btn & g_state_reset_btn) == g_state_reset_btn) {
                if ((g_state_prev & g_state_reset_btn) != g_state_reset_btn &&
                    mesen_bridge_reset() == 0)
                    log_line("[mmlc] console reset");
            }
            g_state_prev = btn;
        }
    }
    {
        unsigned int btn = (r == ERROR_SUCCESS && state) ?
                           *(unsigned short *)((char *)state + 4) : 0;
        /* Log every button change (bounded) so the menu button is discoverable. */
        static unsigned int last[4] = { 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF };
        if (idx < 4 && (g_xig_logs < 8 || btn != last[idx])) {
            last[idx] = btn;
            log_line("[mmlc] xig idx=%lu r=%lu fb=%d btn=%#x",
                     (unsigned long)idx, (unsigned long)r, fb, btn);
        }
        g_xig_logs++;
    }
    return r;
}

static int install_xinput_hook(HMODULE mod)
{
    HMODULE xi = LoadLibraryA("xinput1_3.dll");
    if (!xi) {
        log_line("[mmlc] xinput hook: xinput1_3.dll not found");
        return -1;
    }
    /* Resolve by name first (ordinals differ between Wine builds). */
    g_real_xig = (XInputGetState_t)GetProcAddress(xi, "XInputGetState");
    if (!g_real_xig)
        g_real_xig = (XInputGetState_t)GetProcAddress(xi, (LPCSTR)2);
    if (!g_real_xig) {
        log_line("[mmlc] xinput hook: XInputGetState missing");
        return -1;
    }
    BYTE *base = (BYTE *)mod;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    IMAGE_IMPORT_DESCRIPTOR *imp = (IMAGE_IMPORT_DESCRIPTOR *)(base +
        nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for (; imp->Name; imp++) {
        const char *dll = (const char *)(base + imp->Name);
        if (_stricmp(dll, "XINPUT1_3.dll") != 0)
            continue;
        IMAGE_THUNK_DATA *oft = (IMAGE_THUNK_DATA *)(base + imp->OriginalFirstThunk);
        IMAGE_THUNK_DATA *ft = (IMAGE_THUNK_DATA *)(base + imp->FirstThunk);
        for (; oft->u1.AddressOfData; oft++, ft++) {
            if (IMAGE_SNAP_BY_ORDINAL(oft->u1.Ordinal) &&
                IMAGE_ORDINAL(oft->u1.Ordinal) == 2) {
                DWORD old;
                if (VirtualProtect(&ft->u1.Function, sizeof(void *),
                                   PAGE_READWRITE, &old)) {
                    ft->u1.Function = (ULONG_PTR)hook_XInputGetState;
                    VirtualProtect(&ft->u1.Function, sizeof(void *), old, &old);
                    FlushInstructionCache(GetCurrentProcess(), &ft->u1.Function,
                                          sizeof(void *));
                    g_xinput_hook = 1;
                    log_line("[mmlc] xinput hook installed (all controllers)");
                    return 0;
                }
            }
        }
    }
    log_line("[mmlc] xinput hook: XINPUT1_3 IAT entry not found");
    return -1;
}

/* --- Mesen audio bridge (M6.5): play the host's captured audio via waveOut --- */
static volatile int g_audio_run = 1;
static HWAVEOUT g_wo = NULL;
#define MESEN_AUDIO_BUFS 4
#define MESEN_AUDIO_FRAME 1024   /* samples per waveOut buffer (~21ms) */
static WAVEHDR g_whdr[MESEN_AUDIO_BUFS];
static int16_t g_abuf[MESEN_AUDIO_BUFS][MESEN_AUDIO_FRAME];
static int g_audio_logs = 0;

static DWORD WINAPI mesen_audio_thread(LPVOID p)
{
    (void)p;
    uint32_t sr = 48000;
    int channels = 2;
    while (g_audio_run) {
        Sleep(2);
        if (!mesen_bridge_ready()) { Sleep(50); continue; }
        /* Fetch and accumulate so playback is written in fixed-size buffers. */
        {
            int16_t tmp[4096];
            uint32_t st = 0;
            int n = mesen_bridge_get_audio(tmp, 4096, &sr, &st);
            channels = st ? 2 : 1;
            if (n > 0) {
                if (g_acc_n + n > (int)(sizeof(g_acc) / 2))
                    n = (int)(sizeof(g_acc) / 2) - g_acc_n;
                memcpy(g_acc + g_acc_n, tmp, (size_t)n * 2);
                g_acc_n += n;
            }
        }
        if (!g_wo) {
            WAVEFORMATEX wf;
            memset(&wf, 0, sizeof(wf));
            wf.wFormatTag = WAVE_FORMAT_PCM;
            wf.nChannels = (WORD)channels;
            wf.nSamplesPerSec = sr;
            wf.wBitsPerSample = 16;
            wf.nBlockAlign = (WORD)(channels * 2);
            wf.nAvgBytesPerSec = sr * wf.nBlockAlign;
            if (waveOutOpen(&g_wo, WAVE_MAPPER, &wf, 0, 0, 0) != MMSYSERR_NOERROR) {
                if (g_audio_logs < 3) {
                    g_audio_logs++;
                    log_line("[mmlc] audio: waveOutOpen failed");
                }
                g_wo = NULL;
                Sleep(1000);
                continue;
            }
            for (int i = 0; i < MESEN_AUDIO_BUFS; i++) {
                memset(&g_whdr[i], 0, sizeof(WAVEHDR));
                g_whdr[i].lpData = (LPSTR)g_abuf[i];
                waveOutPrepareHeader(g_wo, &g_whdr[i], sizeof(WAVEHDR));
            }
            log_line("[mmlc] audio: waveOut opened %u Hz %u ch", sr, channels);
        }
        while (g_acc_n >= MESEN_AUDIO_FRAME) {
            int i, freeidx = -1;
            for (i = 0; i < MESEN_AUDIO_BUFS; i++)
                if (!(g_whdr[i].dwFlags & WHDR_INQUEUE)) { freeidx = i; break; }
            if (freeidx < 0) break;
            memcpy(g_abuf[freeidx], g_acc, MESEN_AUDIO_FRAME * 2);
            memmove(g_acc, g_acc + MESEN_AUDIO_FRAME,
                    (size_t)(g_acc_n - MESEN_AUDIO_FRAME) * 2);
            g_acc_n -= MESEN_AUDIO_FRAME;
            g_whdr[freeidx].dwBufferLength = MESEN_AUDIO_FRAME * 2;
            waveOutWrite(g_wo, &g_whdr[freeidx], sizeof(WAVEHDR));
        }
    }
    return 0;
}

/* Hook TextureManagerDX11 slot 9 (UpdateTexture) to swap in Mesen's frame. */
static int install_mesen_tex_hook(HMODULE mod)
{
    BYTE *fn = (BYTE *)mod + MESEN_TEX_RVA;
    /* sanity: expected prologue push ebp; mov ebp,esp */
    if (fn[0] != 0x55 || fn[1] != 0x8b || fn[2] != 0xec) {
        log_line("[mmlc] mesen tex hook: bad prologue at %p", fn);
        return -1;
    }
    const int stolen = 5;
    BYTE *tr = (BYTE *)VirtualAlloc(NULL, stolen + 5, MEM_COMMIT | MEM_RESERVE,
                                    PAGE_EXECUTE_READWRITE);
    if (!tr)
        return -1;
    memcpy(tr, fn, stolen);
    tr[stolen] = 0xE9;
    DWORD back = (DWORD)((fn + stolen) - (tr + stolen + 5));
    memcpy(tr + stolen + 1, &back, 4);
    g_tramp_tex = tr;

    DWORD old;
    if (!VirtualProtect(fn, stolen, PAGE_EXECUTE_READWRITE, &old))
        return -1;
    fn[0] = 0xE9;
    DWORD rel = (DWORD)((BYTE *)mesen_tex_hook - (fn + 5));
    memcpy(fn + 1, &rel, 4);
    VirtualProtect(fn, stolen, old, &old);
    FlushInstructionCache(GetCurrentProcess(), fn, stolen);
    log_line("[mmlc] mesen tex hook installed at %p tramp=%p", fn, tr);
    return 0;
}

/* NOP the single 0xDF stub writer across the mapped sections of a module. */
static int patch_overlay(HMODULE mod)
{
    BYTE *base = (BYTE *)mod;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE)
        return -1;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE)
        return -1;

    int hits = 0;
    IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
    for (WORD i = 0; i < nt->FileHeader.NumberOfSections; i++, sec++) {
        SIZE_T span = sec->Misc.VirtualSize ? sec->Misc.VirtualSize
                                            : sec->SizeOfRawData;
        BYTE *start = base + sec->VirtualAddress;
        for (SIZE_T j = 0; j + 4 <= span; j++) {
            if (start[j] == PAT0 && start[j + 1] == PAT1 &&
                start[j + 2] == PAT2 && start[j + 3] == PAT3) {
                DWORD old;
                if (VirtualProtect(start + j, 4, PAGE_EXECUTE_READWRITE, &old)) {
                    start[j] = 0x90;
                    start[j + 1] = 0x90;
                    start[j + 2] = 0x90;
                    start[j + 3] = 0x90;
                    VirtualProtect(start + j, 4, old, &old);
                    hits++;
                    log_line("[mmlc] overlay NOP at %p (section %s)",
                             start + j, sec->Name);
                } else {
                    log_line("[mmlc] VirtualProtect failed at %p (err %lu)",
                             start + j, GetLastError());
                }
            }
        }
    }
    FlushInstructionCache(GetCurrentProcess(), base,
                          nt->OptionalHeader.SizeOfImage);
    return hits;
}

static DWORD WINAPI init_thread(LPVOID param)
{
    (void)param;
    HMODULE target = GetModuleHandleA("Proteus.exe");
    if (!target)
        target = GetModuleHandleA(NULL);
    g_base = (DWORD)(ULONG_PTR)target;
    log_line("[mmlc] 20XX-0.5 proxy loaded; target=%p pid=%lu", target,
             GetCurrentProcessId());

    char ini[MAX_PATH + 16];
    snprintf(ini, sizeof(ini), "%smmlc.ini", g_dir);

    char romrel[64] = "roms";
    cfg_get(ini, "rom_dir", romrel, sizeof(romrel));
    snprintf(g_romdir, sizeof(g_romdir), "%s", g_dir);
    size_t dl = strlen(g_romdir);
    if (dl + 1 < sizeof(g_romdir) && (dl == 0 || g_romdir[dl - 1] != '\\'))
        g_romdir[dl++] = '\\';
    g_romdir[dl] = 0;
    strncat(g_romdir, romrel, sizeof(g_romdir) - strlen(g_romdir) - 1);
    dl = strlen(g_romdir);
    if (dl + 1 < sizeof(g_romdir) && (dl == 0 || g_romdir[dl - 1] != '\\')) {
        g_romdir[dl++] = '\\';
        g_romdir[dl] = 0;
    }

    if (cfg_flag(ini, "nop_overlay", 1)) {
        int n = patch_overlay(target);
        log_line("[mmlc] nop_overlay: %d site(s)", n);
    } else {
        log_line("[mmlc] nop_overlay disabled by ini");
    }

    if (cfg_flag(ini, "scan", 1))
        scan_virtuals(target);

    g_rom_inject = cfg_flag(ini, "rom_inject", 0);
    if (g_rom_inject)
        log_line("[mmlc] rom_inject: dir=%s", g_romdir);

    g_asset_log = cfg_flag(ini, "asset_log", 0);

    /* Phase 6: connect to the x64 Mesen host (docs/MESEN.md). */
    g_mesen = cfg_flag(ini, "mesen", 0);
    g_mesen_probe = cfg_flag(ini, "mesen_probe", 0);

    /* The asset hook handles the savestate -> Mesen reset; the overlay hook
     * drives per-game ROM handoff for both the Eclipse rom_inject path and the
     * Mesen replacer. */
    if (g_asset_log || g_mesen)
        install_asset_hook(target);
    if (g_rom_inject || g_mesen)
        install_overlay_hook(target);

    if (g_mesen) {
        char port[16] = "";
        cfg_get(ini, "mesen_host", g_mesen_host, sizeof(g_mesen_host));
        if (cfg_get(ini, "mesen_port", port, sizeof(port)) && port[0])
            g_mesen_port = atoi(port);
        int mconn = -1;
        for (int attempt = 0; attempt < 30; attempt++) {
            mconn = mesen_bridge_connect(g_mesen_host, g_mesen_port);
            if (mconn == 0)
                break;
            Sleep(100);  /* host may still be starting */
        }
        if (mconn == 0) {
            log_line("[mmlc] mesen: connected to %s:%d", g_mesen_host, g_mesen_port);
            install_mesen_tex_hook(target);
            /* Record which game instance the front-end is stepping so Mesen
             * follows the game-select carousel. */
            install_advance_hooks();
            /* Freezing the engine's emulation crashes it (its CPU step is
             * load-bearing), so this is off by default and opt-in only. */
            g_mesen_freeze = cfg_flag(ini, "mesen_freeze", 0);
            if (g_mesen_freeze)
                install_advance_freeze(target);
            g_ram_sync = cfg_flag(ini, "mesen_ram_sync", 0);
            /* Let the Mesen host get the CPU: lower the engine's priority. */
            if (cfg_flag(ini, "mesen_engine_lowprio", 1)) {
                SetPriorityClass(GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS);
                log_line("[mmlc] engine priority set to BELOW_NORMAL");
            }
            g_mesen_null_rom = cfg_flag(ini, "mesen_null_rom", 1);
            g_oc_follow = cfg_flag(ini, "mesen_overclock_follow", 1);
            {
                char ov[16] = "";
                if (cfg_get(ini, "mesen_overclock", ov, sizeof(ov)) && ov[0])
                    g_oc_manual = atoi(ov);
                char ot[16] = "";
                if (cfg_get(ini, "mesen_overclock_turbo", ot, sizeof(ot)) && ot[0])
                    g_oc_turbo = atoi(ot);
            }
            if (g_oc_manual >= 0)
                mesen_apply_overclock(1.0f);   /* explicit override */
            log_line("[mmlc] mesen opts: freeze=%d ram_sync=%d null_rom=%d "
                     "overclock(follow=%d manual=%d turbo=%d)",
                     g_mesen_freeze, g_ram_sync, g_mesen_null_rom,
                     g_oc_follow, g_oc_manual, g_oc_turbo);
            {
                HANDLE h = CreateThread(NULL, 0, mesen_engine_probe_thread,
                                        NULL, 0, NULL);
                if (h) CloseHandle(h);
                h = CreateThread(NULL, 0, mesen_options_watch_thread,
                                 NULL, 0, NULL);
                if (h) CloseHandle(h);
                h = CreateThread(NULL, 0, mesen_audio_thread,
                                 NULL, 0, NULL);
                if (h) CloseHandle(h);
                h = CreateThread(NULL, 0, mesen_ui_pause_thread,
                                 NULL, 0, NULL);
                if (h) CloseHandle(h);
            }
            /* M6.3: substitute the per-frame screen via the D3D11 immediate
             * context once the renderer singleton exists. */
            if (cfg_flag(ini, "mesen_d3d", 1)) {
                HANDLE h = CreateThread(NULL, 0, mesen_d3d_watch_thread,
                                        NULL, 0, NULL);
                if (h)
                    CloseHandle(h);
                else
                    log_line("[mmlc] d3d hook: CreateThread failed (%lu)",
                             GetLastError());
            }
        } else {
            log_line("[mmlc] mesen: connect to %s:%d failed (fallback to Eclipse)",
                     g_mesen_host, g_mesen_port);
        }
    }

    /* Make every connected controller drive the game (XInput fallback), and
     * forward the controller to Mesen when Phase 6 is active. */
    g_all_controllers = cfg_flag(ini, "all_controllers", 1);
    g_menu_rb = cfg_flag(ini, "menu_rb", 0);
    {
        char mb[32] = "";
        if (cfg_get(ini, "menu_button", mb, sizeof(mb)) && mb[0]) {
            DWORD b = parse_button(mb);
            if (b) {
                g_menu_btn = b;
                log_line("[mmlc] menu button = %s (%#lx)", mb, (unsigned long)b);
            }
        }
    }
    /* Optional controller remap (btn_a..btn_right). */
    {
        static const struct { const char *key; DWORD *dst; } m[] = {
            { "btn_a", &g_btn_a }, { "btn_b", &g_btn_b },
            { "btn_select", &g_btn_select }, { "btn_start", &g_btn_start },
            { "btn_up", &g_btn_up }, { "btn_down", &g_btn_down },
            { "btn_left", &g_btn_left }, { "btn_right", &g_btn_right },
        };
        char v[32];
        for (int i = 0; i < (int)(sizeof(m) / sizeof(m[0])); i++) {
            if (cfg_get(ini, m[i].key, v, sizeof(v)) && v[0]) {
                DWORD b = parse_button(v);
                if (b) *m[i].dst = b;
            }
        }
    }
    /* Mesen save-state hotkeys (state_save_button / state_load_button / state_slot). */
    {
        char v[64];
        if (cfg_get(ini, "state_save_button", v, sizeof(v)) && v[0]) {
            DWORD b = parse_button_combo(v);
            if (b) g_state_save_btn = b;
        }
        if (cfg_get(ini, "state_load_button", v, sizeof(v)) && v[0]) {
            DWORD b = parse_button_combo(v);
            if (b) g_state_load_btn = b;
        }
        if (cfg_get(ini, "state_reset_button", v, sizeof(v)) && v[0]) {
            DWORD b = parse_button_combo(v);
            if (b) g_state_reset_btn = b;
        }
        if (cfg_get(ini, "state_slot", v, sizeof(v)) && v[0])
            g_state_slot = atoi(v);
    }
    log_line("[mmlc] state hotkeys: save=%#lx load=%#lx reset=%#lx slot=%d",
             (unsigned long)g_state_save_btn, (unsigned long)g_state_load_btn,
             (unsigned long)g_state_reset_btn, g_state_slot);
    g_skip_intro = cfg_flag(ini, "skip_intro", 1);
    if (g_skip_intro)
        install_intro_skip(target);
    if (g_all_controllers || g_mesen)
        install_xinput_hook(target);

    return 0;
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = hinst;
        DisableThreadLibraryCalls(hinst);
        GetModuleFileNameA(hinst, g_dir, MAX_PATH);
        char *slash = strrchr(g_dir, '\\');
        if (slash)
            slash[1] = 0;
        else
            g_dir[0] = 0;
        snprintf(g_log, sizeof(g_log), "%smmlc_proxy.log", g_dir);

        /* Patching the image is cheap; a thread avoids doing work under the
         * loader lock (and lets the main module finish mapping first). */
        HANDLE h = CreateThread(NULL, 0, init_thread, NULL, 0, NULL);
        if (h)
            CloseHandle(h);
    }
    /* On DLL_PROCESS_DETACH do nothing: a blocking socket round-trip under the
     * loader lock can hang process exit. The host stops emulating when the
     * socket closes (see mesen_host.c). */
    return TRUE;
}
