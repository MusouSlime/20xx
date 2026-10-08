/*
 * mesen_bridge.h -- proxy-side (x86 Windows) client for the x64 Linux
 * mesen_host process. Talks loopback TCP; see tools/mesen_host/mesen_proto.h.
 *
 * Part of the Phase 6 Mesen-core swap (docs/MESEN.md). Built into the
 * steam_api.dll proxy when `mesen=1` is set in mmlc.ini.
 */
#ifndef MMLC_MESEN_BRIDGE_H
#define MMLC_MESEN_BRIDGE_H

#include <stdint.h>

/* Connect to 127.0.0.1:port and perform the HELLO handshake. Returns 0 on ok. */
int mesen_bridge_connect(const char *host, int port);

/* True once connected. */
int mesen_bridge_ready(void);

/* Hand the ROM to the host (headerless PRG/CHR + mapper). Returns 0 on ok. */
int mesen_bridge_load_rom(const uint8_t *prg, uint32_t prg_size,
                          const uint8_t *chr, uint32_t chr_size,
                          uint32_t mapper, uint32_t mirroring);

/* Advance/collect one frame. */
int mesen_bridge_run_frame(void);

/* Copy the latest frame (0xAARRGGBB) into out; sets the width and height. */
int mesen_bridge_get_frame(uint32_t *out, int *w, int *h, int out_capacity);

/* Copy 2 KB CPU RAM. */
int mesen_bridge_get_ram(uint8_t *out, int out_size);

/* Controller buttons (MESEN_BTN_* bitmask) for a NES port (0 or 1). */
int mesen_bridge_set_input(int port, uint32_t buttons);

/* Save/load state slot. */
int mesen_bridge_save_state(uint32_t slot);
int mesen_bridge_load_state(uint32_t slot);

/* Soft-reset the console (Mesen restarts at the title screen). */
int mesen_bridge_reset(void);

/* Set emulation speed in percent (100 = original, >100 = turbo). */
int mesen_bridge_set_speed(uint32_t percent);

/* Pause/resume emulation (to stay in sync with the engine). */
int mesen_bridge_pause(void);
int mesen_bridge_resume(void);

/* NES CPU overclock (extra PPU scanlines per frame; 0 = normal). */
int mesen_bridge_set_overclock(uint32_t scanlines);

/* Fetch captured audio samples (interleaved int16). Returns sample count. */
int mesen_bridge_get_audio(int16_t *out, uint32_t max_samples,
                           uint32_t *sample_rate, uint32_t *stereo);

void mesen_bridge_shutdown(void);

#endif /* MMLC_MESEN_BRIDGE_H */
