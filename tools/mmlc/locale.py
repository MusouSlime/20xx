"""Reader/writer for MMLC's custom localization tables.

``locale/Locale.bin`` (magic ``LOCL``) holds a list of string *keys* reduced to
32-bit **Jenkins one-at-a-time** hashes. ``locale/Strings-<lang>.bin`` (magic
``TEXT``) holds the actual text, index-aligned with the hash list. A key is
resolved by hashing it and looking up the index, then reading that index from a
``Strings`` table.

Layouts (little-endian)::

    LOCL: "LOCL" u32 ver u32 hdr08 u32 size-16 u32 n_langs u32 n_strings
          8x [u8 len][chars incl NUL]   (en fr it de es ja ru pt-br)
          n_strings x u32 OAT(key)
    TEXT: "TEXT" u32 ver u32 hdr08 u32 size-16 u32 n_strings u32 blob_size
          n_strings x u32 offset
          blob (NUL-terminated strings)

The ``hdr08`` field is read by the game's loader but only checked non-zero, so
a rebuilt table may preserve it.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional

LOCL_MAGIC = b"LOCL"
TEXT_MAGIC = b"TEXT"

# The header's 0x08 dword is a CRC-32/POSIX (cksum) over everything from 0x10
# to EOF: poly 0x04C11DB7, non-reflected, init 0, length appended little-endian,
# final xor 0xFFFFFFFF. The game rejects a table whose checksum doesn't match,
# so every rebuild must recompute it. (The game's routine is at VA 0x4728b0.)
_CRC_TABLE = []
for _i in range(256):
    _c = _i << 24
    for _ in range(8):
        _c = (((_c << 1) ^ 0x04C11DB7) if (_c & 0x80000000) else (_c << 1)) & 0xFFFFFFFF
    _CRC_TABLE.append(_c)


def cksum(data: bytes) -> int:
    c = 0
    for b in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((c >> 24) ^ b) & 0xFF]
    n = len(data)
    while n:
        c = ((c << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((c >> 24) ^ (n & 0xFF)) & 0xFF]
        n >>= 8
    return (~c) & 0xFFFFFFFF


def oat(key: str) -> int:
    """Jenkins one-at-a-time hash (32-bit) used for locale string keys."""
    h = 0
    for c in key.encode("utf-8"):
        h = (h + c) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    h = (h + (h << 15)) & 0xFFFFFFFF
    return h


class LocaleError(RuntimeError):
    pass


@dataclass
class Locale:
    version: int
    hdr08: int
    langs: List[str]
    hashes: List[int]

    def index_of(self, key: str) -> Optional[int]:
        h = oat(key)
        try:
            return self.hashes.index(h)
        except ValueError:
            return None

    def key_hash(self, index: int) -> int:
        return self.hashes[index]

    def add(self, key: str) -> int:
        """Append *key* and return its new index (== count before adding)."""
        idx = len(self.hashes)
        self.hashes.append(oat(key))
        return idx

    @property
    def count(self) -> int:
        return len(self.hashes)


def parse_locale(data: bytes) -> Locale:
    if data[:4] != LOCL_MAGIC:
        raise LocaleError("not a LOCL table")
    version, hdr08, _size, nlang, nstr = struct.unpack_from("<IIIII", data, 4)
    p = 0x18
    langs: List[str] = []
    for _ in range(nlang):
        n = data[p]
        langs.append(data[p + 1 : p + 1 + n].rstrip(b"\0").decode("ascii", "replace"))
        p += 1 + n
    hashes = [struct.unpack_from("<I", data, p + 4 * i)[0] for i in range(nstr)]
    return Locale(version, hdr08, langs, hashes)


def build_locale(loc: Locale) -> bytes:
    body = bytearray()
    body += LOCL_MAGIC
    body += struct.pack("<III", loc.version, loc.hdr08, 0)  # size-16 patched below
    body += struct.pack("<II", len(loc.langs), loc.count)
    for lang in loc.langs:
        enc = lang.encode("ascii") + b"\0"
        body += bytes([len(enc)]) + enc
    for h in loc.hashes:
        body += struct.pack("<I", h)
    struct.pack_into("<I", body, 0x0C, len(body) - 16)
    struct.pack_into("<I", body, 0x08, cksum(bytes(body[0x10:])))
    return bytes(body)


@dataclass
class Strings:
    lang: str
    version: int
    hdr08: int
    entries: List[bytes] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.entries)

    def get(self, index: int) -> str:
        return self.entries[index].decode("utf-8", "replace")

    def set(self, index: int, text: str) -> None:
        self.entries[index] = text.encode("utf-8")

    def add(self, text: str) -> int:
        idx = len(self.entries)
        self.entries.append(text.encode("utf-8"))
        return idx

    def to_bytes(self) -> bytes:
        count = len(self.entries)
        blob = bytearray()
        offsets: List[int] = []
        for e in self.entries:
            offsets.append(len(blob))
            blob += e + b"\0"
        body = bytearray()
        body += TEXT_MAGIC
        body += struct.pack("<III", self.version, self.hdr08, 0)  # size-16 patched
        body += struct.pack("<II", count, len(blob))
        body += struct.pack(f"<{count}I", *offsets) if count else b""
        body += bytes(blob)
        struct.pack_into("<I", body, 0x0C, len(body) - 16)
        struct.pack_into("<I", body, 0x08, cksum(bytes(body[0x10:])))
        return bytes(body)


def parse_strings(data: bytes, lang: str = "") -> Strings:
    if data[:4] != TEXT_MAGIC:
        raise LocaleError("not a TEXT table")
    version, hdr08, _size, count, blob_size = struct.unpack_from("<IIIII", data, 4)
    off_start = 0x18
    blob_start = off_start + 4 * count
    offsets = [struct.unpack_from("<I", data, off_start + 4 * i)[0] for i in range(count)]
    blob = data[blob_start : blob_start + blob_size]
    entries = []
    for i in range(count):
        s = offsets[i]
        e = offsets[i + 1] if i + 1 < count else blob_size
        entries.append(blob[s:e].rstrip(b"\0"))
    return Strings(lang, version, hdr08, entries)
