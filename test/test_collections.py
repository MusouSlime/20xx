import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

from mmlc import collections as C


class CollectionsTests(unittest.TestCase):
    def test_default_is_mmlc1(self):
        cols = C.normalize([], "/games/Suzy")
        self.assertEqual(len(cols), 1)
        self.assertEqual(cols[0]["kind"], C.KIND_MMLC1)
        self.assertEqual(cols[0]["game_dir"], "/games/Suzy")
        self.assertEqual(len(C.games(cols[0])), 12)

    def test_roundtrip_and_ids(self):
        a = C.make("/a", name="My Games", kind=C.KIND_MMLC1)
        b = C.make("/b", name="My Games", kind=C.KIND_UNKNOWN)
        a["id"], b["id"] = "my-games", "my-games-2"
        cols = C.normalize([a, b])
        self.assertEqual([c["id"] for c in cols], ["my-games", "my-games-2"])
        self.assertEqual(cols[1]["kind"], C.KIND_UNKNOWN)
        self.assertEqual(C.games(cols[1]), [])

    def test_duplicate_ids_are_repaired(self):
        cols = C.normalize([
            {"id": "x", "name": "A", "kind": C.KIND_MMLC1, "game_dir": "/a"},
            {"id": "x", "name": "B", "kind": C.KIND_MMLC1, "game_dir": "/b"},
        ])
        self.assertEqual(len({c["id"] for c in cols}), 2)

    def test_unknown_kind_is_preserved(self):
        cols = C.normalize([{"id": "future", "name": "MMLC2",
                             "kind": "mmlc2", "game_dir": "/c"}])
        self.assertEqual(cols[0]["kind"], C.KIND_UNKNOWN)
        self.assertEqual(cols[0]["game_dir"], "/c")

    def test_detect_kind(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(C.detect_kind(d), C.KIND_UNKNOWN)
            open(os.path.join(d, "Proteus.exe"), "wb").close()
            self.assertEqual(C.detect_kind(d), C.KIND_MMLC1)
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "Proteus.exe.orig.bak"), "wb").close()
            self.assertEqual(C.detect_kind(d), C.KIND_MMLC1)

    def test_twin(self):
        self.assertEqual(C.twin("mm1"), "rk1")
        self.assertEqual(C.twin("rk6"), "mm6")
        self.assertIsNone(C.twin("xx1"))

    def test_new_id(self):
        self.assertEqual(C.new_id("Mega Man", set()), "mega-man")
        self.assertEqual(C.new_id("Mega Man", {"mega-man"}), "mega-man-2")


if __name__ == "__main__":
    unittest.main()
