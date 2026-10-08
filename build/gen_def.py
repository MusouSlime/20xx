#!/usr/bin/env python3
"""Generate a forwarding .def for a steam_api.dll proxy.

Reads the named exports of the real steam_api.dll and emits one forwarder per
name, targeting the renamed ``steam_api_orig.dll``. The proxy then re-exports
the entire surface while still getting DllMain.

Usage:
  gen_def.py <steam_api.dll> [orig_name] > steam_api_proxy.def
"""
from __future__ import annotations

import struct
import sys


def named_exports(path: str):
    d = open(path, "rb").read()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    opt_size = struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", d, opt)[0]
    dd = opt + (96 if magic == 0x10B else 112)
    exp_rva, exp_sz = struct.unpack_from("<II", d, dd)
    if exp_rva == 0:
        return []
    sections = []
    sec_off = opt + opt_size
    for i in range(nsec):
        o = sec_off + i * 40
        vs, va, rs, ro = struct.unpack_from("<IIII", d, o + 8)
        sections.append((va, max(vs, rs), ro))

    def r2o(r):
        for va, span, ro in sections:
            if va <= r < va + span:
                return ro + (r - va)
        raise ValueError(f"rva {r:#x} not in a section")

    e = r2o(exp_rva)
    num_names = struct.unpack_from("<I", d, e + 24)[0]
    name_ptrs = r2o(struct.unpack_from("<I", d, e + 32)[0])
    out = []
    for i in range(num_names):
        nr = struct.unpack_from("<I", d, name_ptrs + 4 * i)[0]
        no = r2o(nr)
        end = d.find(b"\0", no)
        out.append(d[no:end].decode("latin1"))
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    orig = sys.argv[2] if len(sys.argv) > 2 else "steam_api_orig"
    names = named_exports(src)
    print("LIBRARY steam_api")
    print("EXPORTS")
    for n in names:
        print(f"    {n}={orig}.{n}")
    print(f"; {len(names)} forwarders -> {orig}.dll", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
