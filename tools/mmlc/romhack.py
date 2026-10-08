"""ROM-hack patchers (IPS / BPS), patch-only.

Operates on an in-memory copy of a ROM; never bundles patch or game data.
Both formats are supported because community hacks ship either.

    buf = bytearray(rom)
    buf = apply(buf, patch_bytes)          # returns the (possibly resized) ROM
"""
from __future__ import annotations

from typing import Tuple


class RomhackError(RuntimeError):
    pass


# --- IPS -------------------------------------------------------------------- #

def apply_ips(buf: bytearray, ips: bytes, adjust: int = 0) -> bytearray:
    """Apply an IPS patch in place-ish. ``adjust`` shifts every offset (use
    -0x10 when the patch was made against a headerless PRG)."""
    if ips[:5] != b"PATCH":
        raise RomhackError("not an IPS patch (missing PATCH magic)")
    p = 5
    n = len(ips)
    while p + 3 <= n:
        if ips[p : p + 3] == b"EOF":
            break
        off = int.from_bytes(ips[p : p + 3], "big") + adjust
        p += 3
        if p + 2 > n:
            break
        size = int.from_bytes(ips[p : p + 2], "big")
        p += 2
        if off < 0:
            raise RomhackError(f"IPS offset {off:#x} is out of range")
        if size:
            if p + size > n:
                raise RomhackError("truncated IPS record")
            end = off + size
            if end > len(buf):
                buf.extend(b"\x00" * (end - len(buf)))
            buf[off:end] = ips[p : p + size]
            p += size
        else:
            if p + 3 > n:
                raise RomhackError("truncated IPS RLE record")
            rle = int.from_bytes(ips[p : p + 2], "big")
            val = ips[p + 2]
            p += 3
            end = off + rle
            if end > len(buf):
                buf.extend(b"\x00" * (end - len(buf)))
            buf[off:end] = bytes([val]) * rle
    return buf


# --- BPS -------------------------------------------------------------------- #

def _bps_read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    value = 0
    shift = 1
    while True:
        b = data[pos]
        pos += 1
        value += (b & 0x7F) * shift
        if b & 0x80:
            break
        shift <<= 7
        value += shift
    return value, pos


def apply_bps(source: bytes, bps: bytes) -> bytearray:
    """Apply a BPS patch to ``source``; returns the target ROM (may resize)."""
    if bps[:4] != b"BPS1":
        raise RomhackError("not a BPS patch (missing BPS1 magic)")
    p = 4
    source_size, p = _bps_read_varint(bps, p)
    target_size, p = _bps_read_varint(bps, p)
    meta_size, p = _bps_read_varint(bps, p)
    p += meta_size
    if source_size != len(source):
        raise RomhackError(
            f"BPS source size {source_size:#x} != ROM size {len(source):#x}"
        )
    target = bytearray(target_size)
    out = 0
    src_rel = 0
    tgt_rel = 0
    while out < target_size:
        length, p = _bps_read_varint(bps, p)
        if length == 0:
            action = bps[p]
            p += 1
            if action == 0:      # SourceRead
                data, p = _bps_read_varint(bps, p)
                target[out : out + data] = source[out : out + data]
                out += data
            elif action == 1:    # TargetRead
                data, p = _bps_read_varint(bps, p)
                target[out : out + data] = bps[p : p + data]
                p += data
                out += data
            elif action == 2:    # SourceCopy
                data, p = _bps_read_varint(bps, p)
                src_rel += -(data >> 1) if (data & 1) else (data >> 1)
                target[out : out + data] = source[src_rel : src_rel + data]
                src_rel += data
                out += data
            elif action == 3:    # TargetCopy
                data, p = _bps_read_varint(bps, p)
                tgt_rel += -(data >> 1) if (data & 1) else (data >> 1)
                for _ in range(data):
                    target[out] = target[tgt_rel]
                    out += 1
                    tgt_rel += 1
            else:
                raise RomhackError(f"unknown BPS action {action}")
        else:
            target[out : out + length] = source[out : out + length]
            out += length
    return target


# --- dispatch --------------------------------------------------------------- #

def apply(buf: bytearray, patch: bytes, adjust: int = 0) -> bytearray:
    """Apply an IPS or BPS patch. IPS keeps the size; BPS may resize."""
    if patch[:5] == b"PATCH":
        return apply_ips(buf, patch, adjust)
    if patch[:4] == b"BPS1":
        return apply_bps(bytes(buf), patch)
    raise RomhackError("unrecognised patch format (need IPS or BPS)")


def apply_file(buf: bytearray, path: str, adjust: int = 0) -> bytearray:
    with open(path, "rb") as fh:
        return apply(buf, fh.read(), adjust)
