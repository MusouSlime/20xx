import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

_spec = importlib.util.spec_from_file_location(
    "mmlc_20xx", os.path.join(ROOT, "tools", "20xx", "20xx.py"))
X = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(X)


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


class SetupFlowTests(unittest.TestCase):
    def test_install_proxy_backs_up_original(self):
        if not os.path.exists(X.PROXY_BUILD):
            self.skipTest("no built proxy (build/build_proxy.sh)")
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "steam_api.dll")
            orig = os.path.join(d, "steam_api_orig.dll")
            with open(dest, "wb") as fh:
                fh.write(b"VALVE ORIGINAL")
            self.assertTrue(X.install_proxy(d))
            self.assertEqual(_read(orig), b"VALVE ORIGINAL")
            self.assertEqual(_read(dest), _read(X.PROXY_BUILD))
            # Idempotent: the original is not overwritten by the proxy.
            self.assertTrue(X.install_proxy(d))
            self.assertEqual(_read(orig), b"VALVE ORIGINAL")

    def test_install_proxy_refuses_without_valve_dll(self):
        if not os.path.exists(X.PROXY_BUILD):
            self.skipTest("no built proxy")
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(X.install_proxy(d))
            self.assertFalse(os.path.exists(os.path.join(d, "steam_api.dll")))

    def test_setup_env_detection_does_not_crash(self):
        self.assertTrue(isinstance(X.game_dir_default(), str))

    def test_doctor_args_exist(self):
        p = X.build_parser()
        ns = p.parse_args(["doctor"])
        self.assertEqual(ns.cmd, "doctor")
        ns = p.parse_args(["setup", "--yes", "--no-core-build"])
        self.assertEqual(ns.cmd, "setup")
        self.assertTrue(ns.yes)
        self.assertTrue(ns.no_core_build)


if __name__ == "__main__":
    unittest.main()
