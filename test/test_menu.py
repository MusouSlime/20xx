"""Tests for the data.pie / locale / menu modules (no game assets required)."""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

from mmlc import locale as L
from mmlc import menudb, zipaes


class LocaleTests(unittest.TestCase):
    def test_oat_known_values(self):
        self.assertEqual(L.oat("MU-CAT-PROD"), 0x906EE9AF)
        self.assertEqual(L.oat("MU-CAT-CONCEPT"), 0x7A663FC8)
        self.assertEqual(L.oat("MU-CAT-ANTIQUES"), 0xE05C6DA0)

    def test_locale_roundtrip_and_lookup(self):
        keys = ["DB-RM1-NAME-01", "MU-CAT-PROD", "HELLO"]
        loc = L.Locale(1, 0xDEADBEEF, ["en", "pt-br"], [L.oat(k) for k in keys])
        raw = L.build_locale(loc)
        loc2 = L.parse_locale(raw)
        self.assertEqual(raw, L.build_locale(loc2))
        self.assertEqual(loc2.langs, ["en", "pt-br"])
        self.assertEqual(loc2.index_of("MU-CAT-PROD"), 1)
        self.assertIsNone(loc2.index_of("NOPE"))

    def test_strings_roundtrip_and_edit(self):
        s = L.Strings("en", 1, 0x1234, [b"Mega Man", b"Cut Man", b"PRODUCTION ART"])
        raw = s.to_bytes()
        s2 = L.parse_strings(raw, "en")
        self.assertEqual(raw, s2.to_bytes())
        self.assertEqual(s2.get(1), "Cut Man")
        self.assertEqual(s2.count, 3)
        s2.set(1, "Cutman!")
        self.assertEqual(L.parse_strings(s2.to_bytes(), "en").get(1), "Cutman!")

    def test_header_checksum(self):
        # the 0x08 dword must be CRC-32/POSIX over file[0x10:]
        s = L.Strings("en", 1, 0, [b"a", b"bb", b"ccc"])
        raw = s.to_bytes()
        self.assertEqual(struct.unpack_from("<I", raw, 8)[0], L.cksum(raw[0x10:]))
        loc = L.Locale(1, 0, ["en"], [L.oat("K1"), L.oat("K2")])
        raw = L.build_locale(loc)
        self.assertEqual(struct.unpack_from("<I", raw, 8)[0], L.cksum(raw[0x10:]))

    def test_cksum_known_value(self):
        # Strings-en.bin's header checksum, from the local install
        p = "/tmp/opencode/mmlc-assets/locale/Strings-en.bin"
        if not os.path.exists(p):
            self.skipTest("reference asset not present")
        d = open(p, "rb").read()
        self.assertEqual(struct.unpack_from("<I", d, 8)[0], L.cksum(d[0x10:]))

    def test_unicode_roundtrip(self):
        s = L.Strings("ja", 1, 0, ["カットマン".encode(), "ロックマン".encode()])
        raw = s.to_bytes()
        self.assertEqual(L.parse_strings(raw, "ja").get(0), "カットマン")


DB_XML = b"""<database>
  <game name="Mega Man" sprite_folder="Database/RM1" id="1">
    <entry name="DB-RM1-NAME-01" description="DB-RM1-DATA-01" sprite="a" hp="28" ap="01" />
    <entry name="DB-RM1-NAME-02" description="DB-RM1-DATA-02" sprite="b" hp="01" ap="04" weakness_sprite="rockbuster" />
    <entry name="DB-RM1-NAME-03" description="DB-RM1-DATA-03" sprite="c" hp="01" ap="03" weakness_sprite="thunderbeam" />
  </game>
  <game name="Mega Man 2" sprite_folder="Database/RM2" id="2">
    <entry name="DB-RM2-NAME-01" description="DB-RM2-DATA-01" sprite="x" hp="01" ap="01" weakness_sprite="metalblade" />
  </game>
</database>
"""


class MenuDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mmlc-menu-")
        self.path = os.path.join(self.tmp, "database.xml")
        with open(self.path, "wb") as fh:
            fh.write(DB_XML)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_list_roundtrip(self):
        db = menudb.Database.load(self.path)
        self.assertEqual([g.id for g in db.games], ["1", "2"])
        e = db.entry("1", "DB-RM1-NAME-02")
        self.assertEqual(e.weakness_sprite, "rockbuster")
        self.assertEqual(db.entry("1", 0).hp, "28")
        # unchanged round-trip preserves all entries
        db2 = menudb.Database.load(self.path)
        self.assertEqual(len(db2.to_bytes()), len(DB_XML))

    def test_set_fields(self):
        db = menudb.Database.load(self.path)
        e = db.entry("1", "DB-RM1-NAME-01")
        e.set(hp="99", weakness_sprite="iceslasher")
        db.save()
        e2 = menudb.Database.load(self.path).entry("1", "DB-RM1-NAME-01")
        self.assertEqual(e2.hp, "99")
        self.assertEqual(e2.weakness_sprite, "iceslasher")

    def test_randomize_deterministic(self):
        db = menudb.Database.load(self.path)
        c1 = menudb.randomize_weakness(db, "seed-a", ["1"])
        first = {e.name: e.weakness_sprite for e in db.game("1").entries}
        db2 = menudb.Database.load(self.path)
        menudb.randomize_weakness(db2, "seed-a", ["1"])
        second = {e.name: e.weakness_sprite for e in db2.game("1").entries}
        self.assertEqual(first, second)
        self.assertTrue(c1)
        # game 2 untouched
        self.assertEqual(db.game("2").entries[0].weakness_sprite, "metalblade")

    def test_apply_map(self):
        db = menudb.Database.load(self.path)
        changes = menudb.apply_weakness_map(
            db, {"1": {"DB-RM1-NAME-02": "firestorm"}}
        )
        self.assertEqual(db.entry("1", "DB-RM1-NAME-02").weakness_sprite, "firestorm")
        self.assertEqual(len(changes), 1)
        with self.assertRaises(menudb.MenuError):
            menudb.apply_weakness_map(db, {"1": {"NOPE": "x"}})


def _has_7z() -> bool:
    return shutil.which("7z") is not None


@unittest.skipUnless(_has_7z(), "7z not installed (used only to seed a test archive)")
class ZipAesTests(unittest.TestCase):
    PW = "test-password-123"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mmlc-zip-")
        self.pie = os.path.join(self.tmp, "data.pie")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(os.path.join(self.work, "xml"))
        with open(os.path.join(self.work, "xml", "a.xml"), "w") as fh:
            fh.write("<a>1</a>")
        # seed an AES-256 Store archive with 7z; all edits below are native
        subprocess.run(
            ["7z", "a", "-tzip", "-mx0", "-mem=AES256", f"-p{self.PW}",
             self.pie, "xml/a.xml"],
            cwd=self.work, check=True, capture_output=True,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _central(path, name):
        d = open(path, "rb").read()
        nb = name.encode()
        i = d.find(b"PK\x01\x02")
        while i != -1:
            fnlen = struct.unpack_from("<H", d, i + 28)[0]
            if d[i + 46 : i + 46 + fnlen] == nb:
                return d[i : i + 46 + fnlen]
            i = d.find(b"PK\x01\x02", i + 4)
        return None

    def test_native_read_update_preserves_metadata(self):
        self.assertEqual(zipaes.read_entry(self.pie, "xml/a.xml", self.PW), b"<a>1</a>")
        before = self._central(self.pie, "xml/a.xml")

        with open(os.path.join(self.work, "xml", "a.xml"), "w") as fh:
            fh.write("<a>2</a>")
        zipaes.update(self.pie, self.work, ["xml/a.xml"], password=self.PW)

        self.assertEqual(zipaes.read_entry(self.pie, "xml/a.xml", self.PW), b"<a>2</a>")
        after = self._central(self.pie, "xml/a.xml")
        # metadata must be preserved exactly (host OS, external attrs, times)
        self.assertEqual(before[4:16], after[4:16])
        self.assertEqual(before[38:42], after[38:42])

        # and 7z must still read the rebuilt archive
        r = subprocess.run(["7z", "t", f"-p{self.PW}", self.pie],
                           capture_output=True, text=True)
        self.assertIn("Everything is Ok", r.stdout)

    def test_extract(self):
        out = os.path.join(self.tmp, "out")
        zipaes.extract(self.pie, out, password=self.PW)
        with open(os.path.join(out, "xml", "a.xml")) as fh:
            self.assertEqual(fh.read(), "<a>1</a>")

    def test_bad_password(self):
        with self.assertRaises(zipaes.ZipError):
            zipaes.read_entry(self.pie, "xml/a.xml", "wrong-password")


if __name__ == "__main__":
    unittest.main(verbosity=2)
