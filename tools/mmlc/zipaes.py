"""Native ``data.pie`` (WinZip AES-256, AE-2) reader/writer.

MMLC keeps every non-ROM asset in ``data.pie``: a zip whose entries are
encrypted with WinZip AES-256. The password is a fixed, publicly documented
constant that belongs to the install; this module never bundles or ships any
archive contents.

We deliberately do **not** use 7z to rewrite the archive: 7z changes entry
metadata (host OS in "version made by", Unix external attributes, timestamps),
and the game's custom zip reader rejects the result (empty Database / all
"UNKNOWN STRING"). Instead we decrypt/re-encrypt entries ourselves and rebuild
the archive preserving every byte of metadata except the changed content and
the shifted local-header offsets.

WinZip AES entry layout: ``salt(16) | pw_verify(2) | ciphertext | hmac(10)``,
key material from ``PBKDF2-HMAC-SHA1(pw, salt, 1000, 66)`` (AES key 32,
HMAC key 32, verify 2), AES-256-CTR with a little-endian counter starting at 1.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import struct
import zlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .pe import write_file_atomic

DEFAULT_PASSWORD = (
    "P091uWEdwe4lI6StDNMNlkodPGvJ38bL3HW6t3BCMYdFi83FXKu7k0NsHP8caDKS"
)


class ZipError(RuntimeError):
    pass


@dataclass
class Entry:
    name: bytes
    local_hdr: bytearray   # 30 bytes, no filename/extra
    local_extra: bytes
    comp: bytes
    central_hdr: bytearray  # 46 bytes, no filename/extra/comment
    central_extra: bytes
    comment: bytes

    @property
    def method(self) -> int:
        return struct.unpack_from("<H", self.local_hdr, 8)[0]

    @property
    def csize(self) -> int:
        return struct.unpack_from("<I", self.local_hdr, 18)[0]

    @property
    def usize(self) -> int:
        return struct.unpack_from("<I", self.local_hdr, 22)[0]


# --- AES -------------------------------------------------------------------- #

def _derive(pw: bytes, salt: bytes) -> Tuple[bytes, bytes, bytes]:
    dk = PBKDF2HMAC(algorithm=hashes.SHA1(), length=66, salt=salt,
                    iterations=1000).derive(pw)
    return dk[:32], dk[32:64], dk[64:66]


def _ctr(key: bytes, data: bytes) -> bytes:
    """WinZip AES CTR: counter is a 16-byte little-endian value starting at 1.

    (cryptography's modes.CTR increments big-endian, so we drive ECB directly.)
    """
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    out = bytearray()
    ctr = 1
    for off in range(0, len(data), 16):
        ks = enc.update(ctr.to_bytes(16, "little"))
        block = data[off : off + 16]
        out += bytes(a ^ b for a, b in zip(block, ks))
        ctr += 1
    enc.finalize()
    return bytes(out)


def _encrypt(pw: bytes, plain: bytes) -> bytes:
    salt = os.urandom(16)
    key, hkey, pv = _derive(pw, salt)
    ct = _ctr(key, plain)
    auth = hmac.new(hkey, ct, hashlib.sha1).digest()[:10]
    return salt + pv + ct + auth


def _decrypt(pw: bytes, comp: bytes, usize: int) -> bytes:
    salt = comp[:16]
    pv = comp[16:18]
    ct = comp[18 : 18 + usize]
    auth = comp[18 + usize : 18 + usize + 10]
    key, hkey, verify = _derive(pw, salt)
    if verify != pv:
        raise ZipError("bad password (verification mismatch)")
    if hmac.new(hkey, ct, hashlib.sha1).digest()[:10] != auth:
        raise ZipError("authentication failed")
    return _ctr(key, ct)


# --- zip parse/rebuild ------------------------------------------------------ #

def _parse(path: str) -> Tuple[List[Entry], bytes]:
    d = open(path, "rb").read()
    eocd = d.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ZipError("no end-of-central-directory record")
    _, _, _, n_disk, n, cd_size, cd_off, clen = struct.unpack_from("<IHHHHIIH", d, eocd)
    comment = d[eocd + 22 : eocd + 22 + clen]
    if cd_off + cd_size > len(d):
        raise ZipError("central directory out of range")
    entries: List[Entry] = []
    p = cd_off
    for _ in range(n):
        if d[p : p + 4] != b"PK\x01\x02":
            raise ZipError("bad central directory signature")
        fnlen, extralen, cmtlen = struct.unpack_from("<HHH", d, p + 28)
        name = d[p + 46 : p + 46 + fnlen]
        central_extra = d[p + 46 + fnlen : p + 46 + fnlen + extralen]
        comment2 = d[p + 46 + fnlen + extralen : p + 46 + fnlen + extralen + cmtlen]
        lhoff = struct.unpack_from("<I", d, p + 42)[0]
        if d[lhoff : lhoff + 4] != b"PK\x03\x04":
            raise ZipError("bad local header signature")
        lfnlen, lextralen = struct.unpack_from("<HH", d, lhoff + 26)
        local_hdr = bytearray(d[lhoff : lhoff + 30])
        local_extra = d[lhoff + 30 + lfnlen : lhoff + 30 + lfnlen + lextralen]
        csize = struct.unpack_from("<I", d, lhoff + 18)[0]
        data_start = lhoff + 30 + lfnlen + lextralen
        comp = d[data_start : data_start + csize]
        entries.append(
            Entry(name, local_hdr, local_extra, comp,
                  bytearray(d[p : p + 46]), central_extra, comment2)
        )
        p += 46 + fnlen + extralen + cmtlen
    return entries, comment


def _set_aes_method(extra: bytes, method: int) -> bytes:
    """Patch the 'actual method' field of a 0x9901 AES extra block."""
    e = bytearray(extra)
    p = 0
    while p + 4 <= len(e):
        hid, sz = struct.unpack_from("<HH", e, p)
        if hid == 0x9901 and sz >= 7:
            struct.pack_into("<H", e, p + 4 + 5, method)
            break
        p += 4 + sz
    return bytes(e)


def _rebuild(entries: List[Entry], comment: bytes,
             changes: Dict[bytes, bytes], pw: bytes) -> bytes:
    new_comp: Dict[bytes, bytes] = {}
    new_lh: Dict[bytes, bytearray] = {}
    new_le: Dict[bytes, bytes] = {}
    for e in entries:
        lh = bytearray(e.local_hdr)
        le = e.local_extra
        comp = e.comp
        if e.name in changes:
            plain = changes[e.name]
            if e.method == 99:
                comp = _encrypt(pw, plain)
                le = _set_aes_method(le, 0)
                struct.pack_into("<I", lh, 14, 0)  # AE-2: CRC unused
            else:
                comp = plain
                struct.pack_into("<I", lh, 14, zlib.crc32(plain) & 0xFFFFFFFF)
            struct.pack_into("<I", lh, 18, len(comp))
            struct.pack_into("<I", lh, 22, len(plain))
        new_comp[e.name] = comp
        new_lh[e.name] = lh
        new_le[e.name] = le

    out = bytearray()
    offsets: Dict[bytes, int] = {}
    for e in entries:
        offsets[e.name] = len(out)
        out += new_lh[e.name] + e.name + new_le[e.name] + new_comp[e.name]

    cd_off = len(out)
    for e in entries:
        ch = bytearray(e.central_hdr)
        ce = e.central_extra
        if e.name in changes:
            plain = changes[e.name]
            ce = _set_aes_method(ce, 0) if e.method == 99 else ce
            struct.pack_into("<I", ch, 20, len(new_comp[e.name]))
            struct.pack_into("<I", ch, 24, len(plain))
            struct.pack_into("<I", ch, 16,
                             0 if e.method == 99 else zlib.crc32(plain) & 0xFFFFFFFF)
        struct.pack_into("<I", ch, 42, offsets[e.name])
        out += ch + e.name + ce + e.comment

    cd_size = len(out) - cd_off
    n = len(entries)
    out += struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, n, n,
                       cd_size, cd_off, len(comment)) + comment
    return bytes(out)


# --- public API ------------------------------------------------------------- #

def read_entry(pie: str, name: str, password: Optional[str] = DEFAULT_PASSWORD) -> bytes:
    entries, _ = _parse(pie)
    pw = (password or DEFAULT_PASSWORD).encode()
    for e in entries:
        if e.name.decode("utf-8") == name:
            if e.method == 99:
                return _decrypt(pw, e.comp, e.usize)
            return e.comp
    raise ZipError(f"{name} not in archive")


def extract(pie: str, dest: str, password: Optional[str] = DEFAULT_PASSWORD,
            names: Optional[Iterable[str]] = None, sevenz: Optional[str] = None) -> None:
    """Extract *names* (or all files) from *pie* into *dest*."""
    want = set(names) if names else None
    entries, _ = _parse(pie)
    pw = (password or DEFAULT_PASSWORD).encode()
    for e in entries:
        nm = e.name.decode("utf-8")
        if nm.endswith("/"):
            continue
        if want is not None and nm not in want:
            continue
        data = _decrypt(pw, e.comp, e.usize) if e.method == 99 else e.comp
        p = os.path.join(dest, nm)
        os.makedirs(os.path.dirname(p) or dest, exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)


def update(pie: str, workdir: str, names: Iterable[str],
           password: Optional[str] = DEFAULT_PASSWORD,
           sevenz: Optional[str] = None) -> None:
    """Replace *names* (relative to *workdir*) inside *pie*, preserving metadata."""
    names = list(names)
    if not names:
        raise ZipError("no files given to update")
    entries, comment = _parse(pie)
    present = {e.name.decode("utf-8") for e in entries}
    changes: Dict[bytes, bytes] = {}
    for n in names:
        if n not in present:
            raise ZipError(f"{n} not in archive")
        with open(os.path.join(workdir, n), "rb") as fh:
            changes[n.encode("utf-8")] = fh.read()
    pw = (password or DEFAULT_PASSWORD).encode()
    write_file_atomic(pie, _rebuild(entries, comment, changes, pw))


def _dos_time():
    import time
    t = time.localtime()
    return ((t.tm_sec // 2) | (t.tm_min << 5) | (t.tm_hour << 11),
            t.tm_mday | (t.tm_mon << 5) | ((t.tm_year - 1980) << 9))


_AES_EXTRA = bytes.fromhex("0199070002004145030000")  # 0x9901 AE-2, AES-256, Store


def add_files(pie: str, files, password: Optional[str] = DEFAULT_PASSWORD) -> int:
    """Add new entries to *pie* (AES-256 Store), preserving all metadata.

    *files* is an iterable of ``(name, data)``. Existing names are skipped.
    """
    entries, comment = _parse(pie)
    existing = {e.name for e in entries}
    pw = (password or DEFAULT_PASSWORD).encode()
    t, d = _dos_time()
    added = 0
    for name, data in files:
        nb = name.encode("utf-8")
        if nb in existing:
            continue
        comp = _encrypt(pw, data)
        lh = bytearray(30)
        struct.pack_into("<IHHHHHIIIHH", lh, 0, 0x04034B50, 0x33, 1, 99, t, d,
                         0, len(comp), len(data), len(nb), len(_AES_EXTRA))
        ch = bytearray(46)
        struct.pack_into("<IHHHHHHIIIHHHHHII", ch, 0, 0x02014B50, 0x003F,
                         0x33, 1, 99, t, d, 0, len(comp), len(data), len(nb),
                         len(_AES_EXTRA), 0, 0, 0, 0x2000, 0)
        entries.append(Entry(nb, lh, _AES_EXTRA, comp, ch, _AES_EXTRA, b""))
        existing.add(nb)
        added += 1
    if added:
        write_file_atomic(pie, _rebuild(entries, comment, {}, pw))
    return added


def repack(pie: str, workdir: str, password: Optional[str] = DEFAULT_PASSWORD,
           sevenz: Optional[str] = None) -> None:
    """Replace every archive entry that also exists under *workdir*."""
    entries, _ = _parse(pie)
    names = []
    for e in entries:
        nm = e.name.decode("utf-8")
        if nm.endswith("/"):
            continue
        if os.path.exists(os.path.join(workdir, nm)):
            names.append(nm)
    update(pie, workdir, names, password=password)
