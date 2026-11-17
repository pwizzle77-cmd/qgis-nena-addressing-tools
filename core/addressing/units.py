# -*- coding: utf-8 -*-
"""Sequential unit labels for one shared access point. Pure Python."""


SCHEMES = ("increment", "letter", "unit", "alpha", "pair", "building")


def _letters(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    n = max(0, int(index))
    out = ""
    while True:
        out = chr(ord("A") + (n % 26)) + out
        n = n // 26 - 1
        if n < 0:
            break
    return out


def next_unit(scheme: str, index: int, base: int, prefix: str = "", start: int = 1):
    """
    The label for one rooftop in a multi-unit session.

    ``index`` is how many units have already been placed (0 is the next one).
    ``base`` is the street number they share, except for ``increment``, which
    walks 101, 102, 103. ``letter`` walks 101A, 101B in the suffix. ``unit``
    keeps the street number and writes ``1``, ``2`` or ``Unit 1``. ``alpha``
    writes A, B, C into the unit field. ``pair`` writes 1A, 1B. ``building``
    keeps the street number and writes the unit number; the building id is
    chosen on the panel and stored separately.

    :returns: (number, suffix, unit)
    """
    if scheme not in SCHEMES:
        raise ValueError(f"Unknown unit scheme {scheme}")
    index = max(0, int(index))
    base = int(base)
    start = int(start)
    if start < 1:
        start = 1
    prefix = "" if prefix is None else str(prefix)
    if scheme == "letter":
        return base, _letters(start - 1 + index), ""
    if scheme == "alpha":
        return base, "", _letters(start - 1 + index)
    if scheme == "pair":
        return base, "", f"{start}{_letters(index)}"
    if scheme == "unit":
        return base, "", f"{prefix}{start + index}"
    if scheme == "building":
        return base, "", str(start + index)
    return base + index, "", ""


def preview(scheme: str, number: int, suffix: str, unit: str) -> str:
    """Short text for the floating panel."""
    if scheme in ("unit", "pair", "building"):
        return unit
    if scheme == "alpha":
        return f"{number} {unit}".strip()
    if scheme == "letter":
        return f"{number}{suffix}"
    return str(number)
