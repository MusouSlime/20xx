"""Regression tests for the MMLC toolkit.

Point MMLC_TEST_EXE at a Proteus.exe. Both the pristine packed binary and a
clean unpacked one carry identical ROM bytes; a *randomized* install will cause
the per-game extraction checks for modified games to be skipped rather than
fail. Tests always copy the source into a temp dir, so the real binary is never
touched.

Run:  python3 -m unittest discover -s test -v
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

from mmlc import canary, patcher, romtable
from mmlc.backup import BackupManager
from mmlc.pe import PE

_CANDIDATES = [
    os.environ.get("MMLC_TEST_EXE"),
    "/tmp/opencode/mmlc/Proteus.exe",  # pristine packed original (dev rig)
    "/var/home/user/.local/share/Steam/steamapps/common/Suzy/Proteus.exe",
]

EXPECTED_SHA256 = {
    "mm1": "521c16bd8e0dab3836076140a4dc16777f64f269f96cf82590bede245f0f95ab",
    "mm2": "c0a2790f179b400d332febcca4763989154c33f53d2325ea617366bba028efd5",
    "mm3": "89f616f855dee51ea70bd0e98918e7e25857d1dfd44d3821f8e7c2ac0bc58ab5",
    "mm4": "a008c564aeee7d487d21289db5c9dfdcf9dc0b3a4a259987ad06dc0f912b6973",
    "mm5": "d2775d6d37b2888df9c12b827cdb6148a2fbe2fef6a0f72763de33dcfd664397",
    "mm6": "b4350917e5e615f30ea2f96daae6f61a14c64f702edfeb7e61e0dcb0d08c639e",
    "rk1": "e5bbc798798a0f5ae6e6d22a35df2fc3d08cefe6a1b278ed7f1cc325e8ee19e0",
    "rk2": "deb88223311aca8ca566b99d4063ca29d389d939fee455f83afaec97c6f0a5d1",
    "rk3": "152133b3de8d8ef0ec6cc879b16b5e4926ff104ededfb7bd512e848b330a9c70",
    "rk4": "ab4eb162a7bc84a317939b3f952314c9c7d0e6b7065210ff39322a3a13aeaa4c",
    "rk5": "7fe561f59280473d5a942bba3f23309dea412129be28fc712dced6f0a77367d6",
    "rk6": "c8c3877c3d9dcfc793adb6cf9b9d46cc6610305681cc68d04937ee0f835afbd1",
}


def _source_exe() -> str:
    for c in _CANDIDATES:
        if c and os.path.exists(c):
            return c
    raise unittest.SkipTest("no Proteus.exe found; set MMLC_TEST_EXE")


def _is_modded(pe: PE) -> bool:
    for key, want in romtable.ORIGINAL_PRG_CRC.items():
        if patcher.crc32(patcher.read_prg(pe, romtable.resolve(key))) != want:
            return True
    return False


class ToolkitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mmlc-test-")
        cls.exe = os.path.join(cls.tmp, "Proteus.exe")
        shutil.copyfile(_source_exe(), cls.exe)
        cls.source_modded = _is_modded(PE(cls.exe))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        shutil.copyfile(_source_exe(), self.exe)

    def test_extract_matches_known_sha256(self):
        pe = PE(self.exe)
        checked = 0
        for key, want in EXPECTED_SHA256.items():
            g = romtable.resolve(key)
            if patcher.crc32(patcher.read_prg(pe, g)) != romtable.ORIGINAL_PRG_CRC[key]:
                continue  # locally modified; verified separately by canary
            got = hashlib.sha256(patcher.extract_ines(pe, g)).hexdigest()
            self.assertEqual(got, want, f"{key} extraction mismatch")
            checked += 1
        self.assertGreaterEqual(checked, 1)

    def test_canary(self):
        pe = PE(self.exe)
        findings = canary(pe, require_pristine=not self.source_modded)
        self.assertEqual(len(findings), 12)

    def test_canary_detects_modification(self):
        pe = PE(self.exe)
        data = bytearray(pe.data)
        g = romtable.resolve("mm1")
        data[g.prg.offset] ^= 0xFF
        tmp = self.exe + ".mod"
        with open(tmp, "wb") as fh:
            fh.write(data)
        with self.assertRaises(Exception):
            canary(PE(tmp), require_pristine=True)

    def test_overlay_nop_and_idempotent(self):
        pe = PE(self.exe)
        hits = patcher.find_overlay_store(pe.data)
        if not hits:
            self.skipTest("source already NOPed (unpacked+patched install)")
        data = bytearray(pe.data)
        n = patcher.nop_overlay(data)
        self.assertEqual(n, len(hits))
        self.assertEqual(patcher.find_overlay_store(bytes(data)), ())

    def test_write_rom_roundtrip(self):
        pe = PE(self.exe)
        g = romtable.resolve("mm2")
        original = patcher.read_game(pe, g)
        mutated = bytearray(original)
        mutated[0] ^= 0xFF
        data = bytearray(pe.data)
        patcher.write_game(data, g, bytes(mutated))
        with open(self.exe, "wb") as fh:
            fh.write(data)
        pe2 = PE(self.exe)
        self.assertNotEqual(
            patcher.crc32(patcher.read_prg(pe2, g)),
            romtable.ORIGINAL_PRG_CRC["mm2"],
        )
        d3 = bytearray(pe2.data)
        patcher.write_game(d3, g, original)
        with open(self.exe, "wb") as fh:
            fh.write(d3)
        pe4 = PE(self.exe)
        self.assertEqual(patcher.read_game(pe4, g), original)

    def test_write_rom_size_guard(self):
        pe = PE(self.exe)
        data = bytearray(pe.data)
        with self.assertRaises(patcher.PatcherError):
            patcher.write_game(data, romtable.resolve("mm1"), b"\x00" * 16)

    def test_backup_restore(self):
        bm = BackupManager(self.exe)
        entry = bm.backup("unit")
        self.assertTrue(os.path.exists(os.path.join(bm.dir, entry.backup_file)))
        with open(self.exe, "ab") as fh:
            fh.write(b"junk")
        bm.restore("unit")
        with open(self.exe, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), entry.sha256)


if __name__ == "__main__":
    unittest.main(verbosity=2)
