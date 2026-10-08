"""Backup / restore manager for patch-only operation.

Every mutating command snapshots the target file into a sidecar store before
writing, and records a manifest so the operation is reversible.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict
from typing import List, Optional

from .pe import write_file_atomic

STORE_DIRNAME = ".mmlc-backup"
MANIFEST = "manifest.json"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def store_dir(target: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(target)), STORE_DIRNAME)


@dataclass
class BackupEntry:
    label: str
    original_name: str
    backup_file: str
    sha256: str
    size: int
    created: float


class BackupManager:
    def __init__(self, target: str):
        self.target = os.path.abspath(target)
        self.dir = store_dir(target)
        self.manifest_path = os.path.join(self.dir, MANIFEST)

    def _load(self) -> dict:
        if not os.path.exists(self.manifest_path):
            return {"entries": []}
        with open(self.manifest_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, m: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        write_file_atomic(
            self.manifest_path, json.dumps(m, indent=2).encode("utf-8")
        )

    def backup(self, label: str = "manual", force: bool = False) -> BackupEntry:
        if not os.path.exists(self.target):
            raise FileNotFoundError(self.target)
        digest = sha256_file(self.target)
        with open(self.target, "rb") as fh:
            data = fh.read()
        m = self._load()
        for e in m["entries"]:
            if e["sha256"] == digest and not force:
                return BackupEntry(**e)
        name = f"{os.path.basename(self.target)}.{digest[:12]}.bak"
        dest = os.path.join(self.dir, name)
        os.makedirs(self.dir, exist_ok=True)
        if not os.path.exists(dest):
            write_file_atomic(dest, data)
        entry = BackupEntry(
            label=label,
            original_name=os.path.basename(self.target),
            backup_file=name,
            sha256=digest,
            size=len(data),
            created=time.time(),
        )
        m["entries"].append(asdict(entry))
        self._save(m)
        return entry

    def entries(self) -> List[BackupEntry]:
        return [BackupEntry(**e) for e in self._load()["entries"]]

    def restore(self, label: Optional[str] = None) -> str:
        m = self._load()
        if not m["entries"]:
            raise RuntimeError("no backups recorded")
        if label is None:
            e = m["entries"][-1]
        else:
            matches = [x for x in m["entries"] if x["label"] == label]
            if not matches:
                raise KeyError(f"no backup labelled {label!r}")
            e = matches[-1]
        src = os.path.join(self.dir, e["backup_file"])
        if not os.path.exists(src):
            raise FileNotFoundError(src)
        with open(src, "rb") as fh:
            data = fh.read()
        write_file_atomic(self.target, data)
        return e["backup_file"]
