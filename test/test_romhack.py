"""Tests for the IPS/BPS ROM-hack patcher."""
from __future__ import annotations

import os
import sys
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

from mmlc import romhack


def _enc(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v == 0:
            out.append(b | 0x80)
            break
        out.append(b)
        v -= 1
    return bytes(out)


class IpsTests(unittest.TestCase):
    def test_records_and_rle(self):
        src = bytearray(b"HELLO WORLD")
        ips = (b"PATCH"
               + (6).to_bytes(3, "big") + (3).to_bytes(2, "big") + b"IPS"
               + (0).to_bytes(3, "big") + (0).to_bytes(2, "big")
               + (2).to_bytes(2, "big") + b"XX"
               + b"EOF")
        self.assertEqual(bytes(romhack.apply_ips(src, ips)), b"XXLLO IPSLD")

    def test_adjust(self):
        # a patch made against a headerless image, applied at .nes offset 16
        src = bytearray(b"\x00" * 32)
        ips = b"PATCH" + (0).to_bytes(3, "big") + (1).to_bytes(2, "big") + b"Z" + b"EOF"
        out = romhack.apply_ips(src, ips, adjust=16)
        self.assertEqual(out[16], ord("Z"))

    def test_bad_magic(self):
        with self.assertRaises(romhack.RomhackError):
            romhack.apply_ips(bytearray(b"x"), b"NOPE")


class BpsTests(unittest.TestCase):
    def _patch(self, source: bytes, target: bytes, actions: bytes) -> bytes:
        body = bytearray(b"BPS1")
        body += _enc(len(source)) + _enc(len(target)) + _enc(0)
        body += actions
        body += zlib.crc32(source).to_bytes(4, "little")
        body += zlib.crc32(target).to_bytes(4, "little")
        body += zlib.crc32(bytes(body)).to_bytes(4, "little")
        return bytes(body)

    def test_same_size(self):
        source, target = b"ABC", b"ABD"
        actions = _enc(2) + _enc(0) + bytes([1]) + _enc(1) + b"D"
        self.assertEqual(bytes(romhack.apply_bps(source, self._patch(source, target, actions))),
                         target)

    def test_resize(self):
        source, target = b"ABC", b"ABCDEFGH"
        actions = _enc(3) + _enc(0) + bytes([1]) + _enc(5) + b"DEFGH"
        self.assertEqual(bytes(romhack.apply_bps(source, self._patch(source, target, actions))),
                         target)

    def test_size_mismatch(self):
        source, target = b"ABC", b"ABD"
        actions = _enc(2) + _enc(0) + bytes([1]) + _enc(1) + b"D"
        with self.assertRaises(romhack.RomhackError):
            romhack.apply_bps(b"XY", self._patch(source, target, actions))


if __name__ == "__main__":
    unittest.main()
