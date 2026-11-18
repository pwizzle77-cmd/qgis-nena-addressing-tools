# -*- coding: utf-8 -*-
"""NENA Globally Unique ID helpers (pure Python)."""

import re
import uuid
from collections import Counter
from typing import Iterable, Optional, Tuple

_SAFE = re.compile(r"[^0-9A-Za-z._~-]")


def local_id(mode: str = "uuid", fid=None) -> str:
    if mode == "fid" and fid is not None and int(fid) >= 0:
        return str(int(fid))
    return uuid.uuid4().hex


def make_nguid(local: str, agency: str, fmt: str = "urn", layer: str = "RCL") -> str:
    """
    Build an NGUID.

    * ``urn``    -> urn:emergency:uid:gis:RCL:{local}:{agency}   (NENA-STA-006.2)
    * ``legacy`` -> RCL_{local}@{agency}                          (NENA-STA-006.1)
    """
    agency = (agency or "").strip()
    if not agency:
        raise ValueError("An agency identifier (e.g. 'psap.example.gov') is required to build NGUIDs")
    local = _SAFE.sub("", str(local))
    if fmt == "legacy":
        return f"{layer}_{local}@{agency}"
    return f"urn:emergency:uid:gis:{layer}:{local}:{agency}"


_URN = re.compile(r"^urn:emergency:uid:gis:([A-Za-z]+):([^:]+):(.+)$", re.I)
_LEGACY = re.compile(r"^([A-Za-z]+)_([^@\s]+)@(\S+)$")


def parse_nguid(value) -> Optional[Tuple[str, str, str, str]]:
    """(format, layer, local id, agency) of an NGUID, or None."""
    text = str(value or "").strip()
    m = _URN.match(text)
    if m:
        return "urn", m.group(1), m.group(2), m.group(3)
    m = _LEGACY.match(text)
    if m:
        return "legacy", m.group(1), m.group(2), m.group(3)
    return None


def agency_from(values: Iterable) -> Tuple[Optional[str], Optional[str]]:
    """The agency (and NGUID format) most of ``values`` (NGUIDs) use."""
    seen = Counter()
    for value in values:
        parsed = parse_nguid(value)
        if parsed:
            seen[(parsed[3], parsed[0])] += 1
    if not seen:
        return None, None
    (agency, fmt), _n = seen.most_common(1)[0]
    return agency, fmt
