"""Collection registry for the 20xx GUI.

A *collection* is one installed game collection (its folder + the games it
contains). MMLC1 is built in; users can register additional installs/collections
from the GUI. A collection kind without a game table is kept as a placeholder so
the UI can show it as unsupported instead of silently dropping it.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from . import romtable

KIND_MMLC1 = "mmlc1"
KIND_UNKNOWN = "unknown"

# Game-table order for MMLC1: US Mega Man then JP Rockman.
MMLC1_GAMES = ["mm1", "mm2", "mm3", "mm4", "mm5", "mm6",
               "rk1", "rk2", "rk3", "rk4", "rk5", "rk6"]

KIND_LABELS = {
    KIND_MMLC1: "Mega Man Legacy Collection 1",
    KIND_UNKNOWN: "Other collection (no game table yet)",
}

KIND_GAMES = {
    KIND_MMLC1: MMLC1_GAMES,
    KIND_UNKNOWN: [],
}

DEFAULT_APPID = "363440"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "collection"


def new_id(name: str, taken) -> str:
    base = _slug(name)
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def detect_kind(game_dir: str) -> str:
    """Best-effort kind detection from an install folder.

    Only MMLC1 is recognized; anything else is registered as unknown rather
    than being mistaken for MMLC1 (ROM offsets differ per collection).
    """
    if not game_dir:
        return KIND_UNKNOWN
    try:
        names = {n.lower() for n in os.listdir(game_dir)}
    except OSError:
        names = set()
    if "proteus.exe" in names or "proteus.exe.orig.bak" in names:
        return KIND_MMLC1
    return KIND_UNKNOWN


def make(game_dir: str = "", *, name: str = "", kind: str = KIND_MMLC1,
         appid: str = DEFAULT_APPID, cid: str = "") -> Dict:
    kind = kind if kind in KIND_GAMES else KIND_UNKNOWN
    if not name:
        name = ("Mega Man Legacy Collection" if kind == KIND_MMLC1
                else KIND_LABELS.get(kind, "Collection"))
    return {
        "id": cid or _slug(name),
        "name": name,
        "game_dir": game_dir,
        "kind": kind,
        "appid": str(appid or DEFAULT_APPID),
    }


def normalize(entries, legacy_dir: str = "",
              legacy_appid: str = DEFAULT_APPID) -> List[Dict]:
    """Return a validated collection list.

    Falls back to a built-in MMLC1 entry at ``legacy_dir`` when the saved list
    is empty. Unknown/old kinds are preserved as ``unknown``.
    """
    out: List[Dict] = []
    taken = set()
    for raw in entries or []:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind") or KIND_UNKNOWN
        if kind not in KIND_GAMES:
            kind = KIND_UNKNOWN
        cid = str(raw.get("id") or "")
        if not cid or cid in taken:
            cid = new_id(raw.get("name") or kind, taken)
        taken.add(cid)
        out.append({
            "id": cid,
            "name": str(raw.get("name") or KIND_LABELS[kind]),
            "game_dir": str(raw.get("game_dir") or ""),
            "kind": kind,
            "appid": str(raw.get("appid") or legacy_appid or DEFAULT_APPID),
        })
    if not out:
        out.append(make(legacy_dir, name="Mega Man Legacy Collection",
                        kind=KIND_MMLC1, appid=legacy_appid, cid="mmlc1"))
    return out


def games(collection: Dict) -> List[str]:
    """Game keys a collection exposes (empty for unsupported kinds)."""
    return list(KIND_GAMES.get(collection.get("kind", KIND_UNKNOWN), []))


def game_title(key: str) -> str:
    game = romtable.GAMES.get(key)
    return game.title if game else key.upper()


def twin(key: str) -> Optional[str]:
    """JP/US counterpart key (mm1 <-> rk1), or None."""
    if len(key) == 3 and key[:2] in ("mm", "rk") and key[2].isdigit():
        other = ("rk" if key.startswith("mm") else "mm") + key[2]
        return other if other in romtable.GAMES else None
    return None


def has_rom_source(game_dir: str) -> bool:
    return any(os.path.exists(os.path.join(game_dir, n))
               for n in ("Proteus.exe.orig.bak", "Proteus.exe"))
