# -*- coding: utf-8 -*-
"""NENA Globally Unique ID helpers (pure Python)."""

import re
import uuid

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
