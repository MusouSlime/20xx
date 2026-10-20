"""Integration test for the portable Mesen host (Linux).

Builds a tiny mock Mesen core and the host, then exercises the loopback
protocol: HELLO, RUN_FRAME, GET_RAM, SHUTDOWN.
"""
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOST_SRC = os.path.join(ROOT, "tools", "mesen_host", "mesen_host.c")

MAGIC = 0x4D4D4E53
HDR = struct.Struct("<IHHII")
CMD_HELLO = 1
CMD_HELLO_ACK = 2
CMD_RUN_FRAME = 4
CMD_SHUTDOWN = 9
CMD_ACK = 10
CMD_GET_RAM = 12

MOCK_CORE = r"""
#include <stdint.h>
#include <stdbool.h>
#include <string.h>

static uint32_t frame[256 * 240];
static uint32_t seq;

void InitDll(void) {}
void InitializeEmu(const char *a, void *b, void *c, bool d, bool e,
                   bool f, bool g) { (void)a; (void)b; (void)c; }
void EnableCaptureRenderer(bool on) { (void)on; }
bool LoadRom(char *path, char *patch) { (void)path; (void)patch; return true; }
bool IsRunning(void) { return true; }
void Stop(void) {}
void Release(void) {}
uint32_t *GetVideoBuffer(uint32_t *w, uint32_t *h, uint32_t *s) {
    *w = 256; *h = 240; *s = ++seq; return frame;
}
void GetMemoryValues(int type, uint32_t addr, uint32_t len, uint8_t *out) {
    (void)type; (void)addr; memset(out, 0x5a, len);
}
void ConfigureNesInput(void) {}
void InitializeDebugger(void) {}
"""


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("host closed the connection")
        buf += chunk
    return buf


def _recv_msg(sock):
    magic, ver, cmd, length, status = HDR.unpack(_recv_exact(sock, HDR.size))
    body = _recv_exact(sock, length) if length else b""
    return magic, ver, cmd, status, body


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux host test")
@unittest.skipUnless(shutil.which("cc"), "needs a C compiler")
class MesenHostTests(unittest.TestCase):
    def test_handshake_and_ram(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = os.path.join(tmp, "mock_core.so")
            host = os.path.join(tmp, "mesen_host")
            cs = os.path.join(tmp, "core.c")
            with open(cs, "w") as fh:
                fh.write(MOCK_CORE)
            subprocess.check_call(["cc", "-shared", "-fPIC", "-O2", "-o", core, cs])
            subprocess.check_call(["cc", "-O2", "-o", host, HOST_SRC,
                                   "-ldl", "-lpthread"])
            port = _free_port()
            proc = subprocess.Popen(
                [host, "--core", core, "--port", str(port),
                 "--home", os.path.join(tmp, "home")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.time() + 5
                sock = None
                while time.time() < deadline:
                    try:
                        sock = socket.create_connection(("127.0.0.1", port), 0.2)
                        break
                    except OSError:
                        time.sleep(0.05)
                self.assertIsNotNone(sock, "host did not start listening")
                with sock:
                    sock.settimeout(2)
                    sock.sendall(HDR.pack(MAGIC, 1, CMD_HELLO, 0, 0))
                    magic, _ver, cmd, status, body = _recv_msg(sock)
                    self.assertEqual(magic, MAGIC)
                    self.assertEqual(cmd, CMD_HELLO_ACK)
                    self.assertEqual(status, 0)
                    self.assertEqual(len(body), 4)

                    sock.sendall(HDR.pack(MAGIC, 1, CMD_RUN_FRAME, 0, 0))
                    magic, _ver, cmd, status, _ = _recv_msg(sock)
                    self.assertEqual((magic, cmd, status), (MAGIC, CMD_ACK, 0))

                    sock.sendall(HDR.pack(MAGIC, 1, CMD_GET_RAM, 0, 0))
                    magic, _ver, cmd, status, body = _recv_msg(sock)
                    self.assertEqual((magic, cmd, status), (MAGIC, CMD_ACK, 0))
                    self.assertEqual(len(body), 0x800)
                    self.assertEqual(body, b"\x5a" * 0x800)

                    sock.sendall(HDR.pack(MAGIC, 1, CMD_SHUTDOWN, 0, 0))
                    magic, _ver, cmd, status, _ = _recv_msg(sock)
                    self.assertEqual((magic, cmd, status), (MAGIC, CMD_ACK, 0))
                self.assertEqual(proc.wait(timeout=5), 0)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
