import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

from mmlc import setup_env


class SetupEnvTests(unittest.TestCase):
    def test_library_paths_and_escaped_windows_path(self):
        with tempfile.TemporaryDirectory() as d:
            vdf = os.path.join(d, "libraryfolders.vdf")
            with open(vdf, "w", encoding="utf-8") as fh:
                fh.write(
                    '"libraryfolders"\n{\n'
                    '  "0"\n  {\n    "path" "C:\\\\Program Files (x86)\\\\Steam"\n  }\n'
                    '  "1"\n  {\n    "path" "/games/SteamLibrary"\n  }\n}\n')
            paths = setup_env.library_paths(vdf)
            self.assertEqual(paths[0], r"C:\Program Files (x86)\Steam")
            self.assertEqual(paths[1], "/games/SteamLibrary")

    def test_bad_or_missing_vdf(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(setup_env.library_paths(os.path.join(d, "nope")), [])
            p = os.path.join(d, "x.vdf")
            with open(p, "w") as fh:
                fh.write("not a vdf")
            self.assertEqual(setup_env.library_paths(p), [])

    def test_install_candidates_follow_libraries(self):
        with tempfile.TemporaryDirectory() as root, \
                tempfile.TemporaryDirectory() as lib:
            os.makedirs(os.path.join(root, "steamapps"))
            with open(os.path.join(root, "steamapps", "libraryfolders.vdf"),
                      "w", encoding="utf-8") as fh:
                fh.write(f'"libraryfolders"\n{{\n "1"\n {{\n  "path" "{lib}"\n }}\n}}\n')
            cands = setup_env.install_candidates([root])
            self.assertIn(os.path.join(lib, "steamapps", "common", "Suzy"), cands)

    def test_find_install_requires_proteus(self):
        with tempfile.TemporaryDirectory() as root:
            game = os.path.join(root, "steamapps", "common", "Suzy")
            os.makedirs(game)
            self.assertIsNone(setup_env.find_install([root]))
            open(os.path.join(game, "Proteus.exe.orig.bak"), "wb").close()
            self.assertEqual(setup_env.find_install([root]), game)

    def test_proxy_installed(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(setup_env.proxy_installed(d))
            open(os.path.join(d, "steam_api_orig.dll"), "wb").close()
            with open(os.path.join(d, "steam_api.dll"), "wb") as fh:
                fh.write(b"xx [mmlc] xx")
            self.assertTrue(setup_env.proxy_installed(d))
            with open(os.path.join(d, "steam_api.dll"), "wb") as fh:
                fh.write(b"Valve DLL")
            self.assertFalse(setup_env.proxy_installed(d))


if __name__ == "__main__":
    unittest.main()
