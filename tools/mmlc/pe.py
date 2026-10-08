"""Minimal PE32 reader: section map, RVA<->file offset, safe patching.

Only what the MMLC toolchain needs. Read-only by default; callers that write
must go through :func:`write_file_atomic`.
"""
from __future__ import annotations

import os
import struct
import tempfile
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class Section:
    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int

    @property
    def span(self) -> int:
        return max(self.virtual_size, self.raw_size)


class PE:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            self.data = fh.read()
        self._parse()

    def _parse(self) -> None:
        d = self.data
        if d[:2] != b"MZ":
            raise ValueError(f"{self.path}: not a PE (missing MZ)")
        pe_off = struct.unpack_from("<I", d, 0x3C)[0]
        if d[pe_off : pe_off + 4] != b"PE\0\0":
            raise ValueError(f"{self.path}: invalid PE signature")
        machine, num_sections = struct.unpack_from("<HH", d, pe_off + 4)
        opt_size = struct.unpack_from("<H", d, pe_off + 20)[0]
        opt_off = pe_off + 24
        magic = struct.unpack_from("<H", d, opt_off)[0]
        if magic != 0x10B:
            raise ValueError(f"{self.path}: expected PE32 (0x10b), got {magic:#x}")
        self.machine = machine
        self.image_base = struct.unpack_from("<I", d, opt_off + 28)[0]
        self.entry_point = struct.unpack_from("<I", d, opt_off + 16)[0]
        self.size_of_image = struct.unpack_from("<I", d, opt_off + 56)[0]
        self.sections: List[Section] = []
        sec_off = opt_off + opt_size
        for i in range(num_sections):
            o = sec_off + i * 40
            name = d[o : o + 8].split(b"\0")[0].decode("latin1", "replace")
            vsize, va, rsize, raw = struct.unpack_from("<IIII", d, o + 8)
            self.sections.append(Section(name, va, vsize, raw, rsize))

    # -- address translation -------------------------------------------------
    def section_for_va(self, va: int) -> Optional[Section]:
        for s in self.sections:
            if s.virtual_address <= va < s.virtual_address + s.span:
                return s
        return None

    def va_to_off(self, va: int) -> Optional[int]:
        s = self.section_for_va(va)
        if s is None:
            return None
        return s.raw_offset + (va - s.virtual_address)

    def off_to_va(self, off: int) -> Optional[int]:
        for s in self.sections:
            if s.raw_offset <= off < s.raw_offset + s.raw_size:
                return s.virtual_address + (off - s.raw_offset)
        return None

    def off_to_rva(self, off: int) -> Optional[int]:
        va = self.off_to_va(off)
        if va is None:
            return None
        return va - self.image_base

    # -- helpers -------------------------------------------------------------
    def read_at(self, offset: int, size: int) -> bytes:
        if offset < 0 or offset + size > len(self.data):
            raise ValueError(f"read out of range: {offset:#x}+{size:#x}")
        return self.data[offset : offset + size]

    def find(self, needle: bytes, start: int = 0, end: Optional[int] = None) -> int:
        return self.data.find(needle, start, len(self.data) if end is None else end)

    def find_all(self, needle: bytes) -> List[int]:
        out: List[int] = []
        i = self.data.find(needle)
        while i != -1:
            out.append(i)
            i = self.data.find(needle, i + 1)
        return out

    def notes(self) -> str:
        lines = [
            f"file            : {self.path}",
            f"size            : {len(self.data)} bytes ({len(self.data):#x})",
            f"machine         : {self.machine:#06x}",
            f"image base      : {self.image_base:#010x}",
            f"entry point RVA : {self.entry_point:#010x}",
            f"size of image   : {self.size_of_image:#010x}",
        ]
        for s in self.sections:
            lines.append(
                f"  {s.name:10} VA={s.virtual_address:#010x} "
                f"VS={s.virtual_size:#010x} RAW={s.raw_offset:#010x} "
                f"RS={s.raw_size:#010x}"
            )
        return "\n".join(lines)


def write_file_atomic(path: str, data: bytes) -> None:
    """Write bytes to *path* via a temp file + rename, preserving nothing else."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".mmlc-tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
