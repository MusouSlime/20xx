/*
 * mesen_proto.h -- wire protocol between the steam_api.dll proxy (x86, under
 * Proton) and the x64 Linux Mesen host process.
 *
 * Transport: loopback TCP (127.0.0.1). Length-prefixed, little-endian.
 * Bulk frame/RAM data lives in POSIX shared memory (/dev/shm/mmlc_*); the
 * socket carries commands and small replies only.
 *
 * This header is shared by both sides and must stay C89-clean and free of any
 * platform types beyond <stdint.h>.
 */
#ifndef MMLC_MESEN_PROTO_H
#define MMLC_MESEN_PROTO_H

#include <stdint.h>

#define MESEN_PROTO_MAGIC 0x4D4D4E53u /* "MMNS" */
#define MESEN_PROTO_VERSION 1u

/* Defaults. Overridable via mmlc.ini (mesen_host / mesen_port). */
#define MESEN_DEFAULT_PORT 36344
#define MESEN_FRAME_SHM "/mmlc_mesen_frame"
#define MESEN_RAM_SHM   "/mmlc_mesen_ram"
#define MESEN_AUDIO_SHM "/mmlc_mesen_audio"

/* NES video is 256x240; Mesen may output a slightly larger overscan frame. */
#define MESEN_FRAME_MAX_W 256
#define MESEN_FRAME_MAX_H 240
#define MESEN_FRAME_PIXELS (MESEN_FRAME_MAX_W * MESEN_FRAME_MAX_H)

enum mesen_cmd {
    MESEN_CMD_HELLO       = 1,  /* proxy -> host: magic, version, shm names  */
    MESEN_CMD_HELLO_ACK   = 2,  /* host  -> proxy: magic, version, core ver  */
    MESEN_CMD_LOAD_ROM    = 3,  /* proxy -> host: prg+chr bytes (inline)     */
    MESEN_CMD_RUN_FRAME   = 4,  /* proxy -> host: advance one frame          */
    MESEN_CMD_SET_INPUT   = 5,  /* proxy -> host: controller bits            */
    MESEN_CMD_SAVE_STATE  = 6,  /* proxy -> host: save slot                   */
    MESEN_CMD_LOAD_STATE  = 7,  /* proxy -> host: load slot                   */
    MESEN_CMD_RESET       = 8,  /* proxy -> host: soft reset                  */
    MESEN_CMD_SHUTDOWN    = 9,  /* proxy -> host: stop + release             */
    MESEN_CMD_ACK         = 10, /* host  -> proxy: generic ack + status      */
    MESEN_CMD_GET_FRAME   = 11, /* proxy -> host: reply = frame inline       */
    MESEN_CMD_GET_RAM     = 12, /* proxy -> host: reply = 2 KB CPU RAM       */
    MESEN_CMD_SET_SPEED   = 13, /* proxy -> host: emulation speed (percent)  */
    MESEN_CMD_PAUSE       = 14, /* proxy -> host: pause emulation            */
    MESEN_CMD_RESUME      = 15, /* proxy -> host: resume emulation           */
    MESEN_CMD_SET_OVERCLOCK = 16, /* proxy -> host: NES overclock scanlines  */
    MESEN_CMD_GET_AUDIO   = 17, /* proxy -> host: reply = int16 audio samples */
};

/* Controller bit layout (NES order, matching $4016). */
enum mesen_buttons {
    MESEN_BTN_A      = 1u << 0,
    MESEN_BTN_B      = 1u << 1,
    MESEN_BTN_SELECT = 1u << 2,
    MESEN_BTN_START  = 1u << 3,
    MESEN_BTN_UP     = 1u << 4,
    MESEN_BTN_DOWN   = 1u << 5,
    MESEN_BTN_LEFT   = 1u << 6,
    MESEN_BTN_RIGHT  = 1u << 7,
};

/* Fixed-size message header. Payload follows immediately. */
typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t cmd;
    uint32_t length;   /* payload bytes */
    uint32_t status;   /* reply/status code */
} mesen_msg_hdr;

/* MESEN_CMD_LOAD_ROM payload: headerless PRG then CHR. */
typedef struct {
    uint32_t prg_size;
    uint32_t chr_size;
    uint32_t mapper;      /* iNES mapper number */
    uint32_t mirroring;   /* 0=h, 1=v, 2=four-screen */
    uint32_t flags;       /* reserved */
    /* uint8_t prg[prg_size]; uint8_t chr[chr_size]; */
} mesen_load_rom;

/* Shared frame buffer (MESEN_FRAME_SHM). */
typedef struct {
    uint32_t seq;                 /* incremented per published frame */
    uint32_t width;
    uint32_t height;
    uint32_t pitch;               /* pixels per row (== width) */
    uint32_t format;              /* 0 = 0xAARRGGBB */
    uint32_t palette_version;
    uint32_t frame[1];            /* frame[pitch * height], packed */
} mesen_frame_shm;

/* Shared NES RAM mirror (MESEN_RAM_SHM): 2 KB CPU RAM + PPU/APU windows. */
typedef struct {
    uint32_t seq;
    uint8_t  cpu_ram[0x800];
    uint8_t  ppu_oam[0x100];
    uint8_t  ppu_palette[0x20];
    uint8_t  apu_regs[0x18];
} mesen_ram_shm;

#endif /* MMLC_MESEN_PROTO_H */
