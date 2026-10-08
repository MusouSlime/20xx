"""Toolkit configuration (``key=value`` ini).

Search order (first existing wins):
  * ``$MMLC_CONFIG``
  * ``./mmlc.tool.ini``
  * ``~/.config/mmlc/mmlc.ini``
  * ``<repo>/config/mmlc.tool.ini``

Recognised keys: ``pie_password``, ``sevenz``.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

_REPO_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "mmlc.tool.ini",
)


def _candidates() -> list:
    out = []
    if os.environ.get("MMLC_CONFIG"):
        out.append(os.environ["MMLC_CONFIG"])
    out.append(os.path.join(os.getcwd(), "mmlc.tool.ini"))
    out.append(os.path.join(os.path.expanduser("~"), ".config", "mmlc", "mmlc.ini"))
    out.append(_REPO_CONFIG)
    return out


def load(path: Optional[str] = None) -> Dict[str, str]:
    if path:
        files = [path]
    else:
        files = _candidates()
    for f in files:
        if f and os.path.exists(f):
            return _parse(f)
    return {}


def _parse(path: str) -> Dict[str, str]:
    cfg: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line[0] in "#;":
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            cfg[key.strip().lower()] = value.strip()
    return cfg


def get(key: str, default: Optional[str] = None, path: Optional[str] = None) -> Optional[str]:
    return load(path).get(key.lower(), default)
