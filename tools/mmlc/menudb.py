"""Museum / Database menu data model.

The Museum and Database screens are plain XML inside ``data.pie``::

    xml/database.xml   <database><game id sprite_folder><entry .../></game></database>
    xml/museum_cc.xml  <museum><game sprite_folder><category/><art .../></game></museum>
    xml/museum_nx.xml  (Switch variant, same schema)

``entry/@name`` and ``entry/@description`` are locale keys (see
:mod:`mmlc.locale`); ``@sprite``/``@weakness_sprite`` are PNG basenames under
the game's ``sprite_folder``. This module keeps the underlying ElementTree so
unknown attributes survive round-trips.
"""
from __future__ import annotations

import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Union


class MenuError(RuntimeError):
    pass


def _load_tree(path: str) -> ET.ElementTree:
    try:
        return ET.parse(path)
    except ET.ParseError as exc:
        raise MenuError(f"{path}: {exc}") from exc


def _indent(tree: ET.ElementTree) -> None:
    try:
        ET.indent(tree, space="  ")
    except AttributeError:  # pragma: no cover - py<3.9
        pass


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

@dataclass
class Entry:
    el: ET.Element

    @property
    def name(self) -> str:
        return self.el.get("name", "")

    @property
    def description(self) -> Optional[str]:
        return self.el.get("description")

    @property
    def sprite(self) -> Optional[str]:
        return self.el.get("sprite")

    @property
    def hp(self) -> Optional[str]:
        return self.el.get("hp")

    @property
    def ap(self) -> Optional[str]:
        return self.el.get("ap")

    @property
    def weakness_sprite(self) -> Optional[str]:
        return self.el.get("weakness_sprite")

    @property
    def save_state(self) -> Optional[str]:
        return self.el.get("save_state")

    def set(self, **fields: Optional[str]) -> None:
        for key, value in fields.items():
            if value is None:
                continue
            self.el.set(key, str(value))

    def as_dict(self) -> Dict[str, Optional[str]]:
        return dict(self.el.attrib)


@dataclass
class Game:
    el: ET.Element

    @property
    def name(self) -> str:
        return self.el.get("name", "")

    @property
    def sprite_folder(self) -> Optional[str]:
        return self.el.get("sprite_folder")

    @property
    def id(self) -> Optional[str]:
        return self.el.get("id")

    @property
    def entries(self) -> List[Entry]:
        return [Entry(e) for e in self.el.findall("entry")]


class Database:
    def __init__(self, path: str, tree: ET.ElementTree):
        self.path = path
        self.tree = tree

    @classmethod
    def load(cls, path: str) -> "Database":
        tree = _load_tree(path)
        if tree.getroot().tag != "database":
            raise MenuError(f"{path}: root is <{tree.getroot().tag}>, expected <database>")
        return cls(path, tree)

    @property
    def games(self) -> List[Game]:
        return [Game(g) for g in self.tree.getroot().findall("game")]

    def game(self, key: Union[str, int]) -> Game:
        key = str(key)
        for g in self.games:
            if g.id == key or g.name == key:
                return g
        raise MenuError(f"no game {key!r}")

    def entry(self, game: Union[str, int], key: Union[str, int]) -> Entry:
        g = self.game(game)
        entries = g.entries
        if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
            idx = int(key)
            if not 0 <= idx < len(entries):
                raise MenuError(f"game {g.id}: entry index {idx} out of range")
            return entries[idx]
        for e in entries:
            if e.name == key:
                return e
        raise MenuError(f"game {g.id}: no entry {key!r}")

    def to_bytes(self) -> bytes:
        _indent(self.tree)
        return ET.tostring(self.tree.getroot(), encoding="utf-8", xml_declaration=False) + b"\n"

    def save(self, path: Optional[str] = None) -> None:
        with open(path or self.path, "wb") as fh:
            fh.write(self.to_bytes())


# --------------------------------------------------------------------------- #
# Weakness sync
# --------------------------------------------------------------------------- #

def randomize_weakness(
    db: Database,
    seed: Union[int, str],
    games: Optional[Iterable[Union[str, int]]] = None,
) -> List[str]:
    """Permute ``weakness_sprite`` among the entries that already define one.

    The permutation is deterministic per ``seed`` + game id, so the same seed
    reproduces the same Database. Returns human-readable change lines.

    NOTE: this is a stand-in for a ROM randomizer's own mapping; when the
    randomizer emits a spoiler, prefer :func:`apply_weakness_map`.
    """
    wanted = {str(g) for g in games} if games else None
    changes: List[str] = []
    for g in db.games:
        if wanted and g.id not in wanted and g.name not in wanted:
            continue
        entries = g.entries
        idxs = [i for i, e in enumerate(entries) if e.weakness_sprite]
        values = [entries[i].weakness_sprite for i in idxs]
        rng = random.Random(f"mmlc:{seed}:{g.id}")
        rng.shuffle(values)
        for i, v in zip(idxs, values):
            old = entries[i].weakness_sprite
            entries[i].el.set("weakness_sprite", v)
            if old != v:
                changes.append(f"game {g.id} {entries[i].name}: {old} -> {v}")
    return changes


def apply_weakness_map(db: Database, mapping: Dict[str, Dict[str, str]]) -> List[str]:
    """Apply an explicit ``{game_id: {entry_name: weapon}}`` mapping."""
    changes: List[str] = []
    for gid, entries in mapping.items():
        g = db.game(gid)
        by_name = {e.name: e for e in g.entries}
        for name, weapon in entries.items():
            if name not in by_name:
                raise MenuError(f"game {gid}: no entry {name!r}")
            e = by_name[name]
            old = e.weakness_sprite
            e.el.set("weakness_sprite", weapon)
            changes.append(f"game {gid} {name}: {old} -> {weapon}")
    return changes


# --------------------------------------------------------------------------- #
# Museum
# --------------------------------------------------------------------------- #

@dataclass
class Art:
    el: ET.Element

    @property
    def name(self) -> str:
        return self.el.get("name", "")

    @property
    def sprite(self) -> str:
        return self.el.get("sprite", "")


class Museum:
    def __init__(self, path: str, tree: ET.ElementTree):
        self.path = path
        self.tree = tree

    @classmethod
    def load(cls, path: str) -> "Museum":
        tree = _load_tree(path)
        if tree.getroot().tag != "museum":
            raise MenuError(f"{path}: root is <{tree.getroot().tag}>, expected <museum>")
        return cls(path, tree)

    @property
    def games(self) -> List[Game]:
        return [Game(g) for g in self.tree.getroot().findall("game")]

    def art(self, game: Union[str, int]) -> List[Art]:
        g = next((g for g in self.games if g.name == str(game)), None)
        if g is None:
            raise MenuError(f"no museum game {game!r}")
        return [Art(a) for a in g.el.findall("art")]

    def to_bytes(self) -> bytes:
        _indent(self.tree)
        return ET.tostring(self.tree.getroot(), encoding="utf-8", xml_declaration=False) + b"\n"

    def save(self, path: Optional[str] = None) -> None:
        with open(path or self.path, "wb") as fh:
            fh.write(self.to_bytes())
