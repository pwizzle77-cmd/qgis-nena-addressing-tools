# -*- coding: utf-8 -*-
"""
Compare centerline sides to a telephone-company MSAG extract.

The extract is any layer already in the project (a CSV loaded as a table is
enough). Each row is a street, a community, a low–high range, and an odd/even
flag. A centerline side matches when its name, community, and parity agree
and its whole range sits inside one of those rows.
"""

from collections import defaultdict
from typing import Dict, List, Sequence

from qgis.core import QgsVectorLayer

from ..compat import py_value
from . import rules as V
from .config import PluginConfig
from .layer_access import ConfigError, FieldAccessor
from .project_config import MsagSettings, resolve_layer
from .ranging import BOTH, ZERO, is_zero_range, normalize_parity
from .schema import STREET_NAME_FIELDS
from .streets.names import TYPE_ABBREVIATIONS, clean, name_key

_DIRS = {}


def _direction_words() -> Dict[str, str]:
    global _DIRS
    if not _DIRS:
        from .schema import DIRECTIONAL_ABBREVIATIONS
        _DIRS = {k.upper().replace(".", ""): v.upper() for k, v in DIRECTIONAL_ABBREVIATIONS.items()}
    return _DIRS


def loose_street_key(text) -> str:
    """'N MAIN ST' and 'North Main Street' share one key."""
    words = clean(text).upper().replace(".", "").split()
    dirs = _direction_words()
    out = []
    for word in words:
        if word in dirs:
            out.append(dirs[word])
        elif word in TYPE_ABBREVIATIONS:
            out.append(TYPE_ABBREVIATIONS[word])
        else:
            out.append(word)
    return " ".join(out)


def compare_msag(layer: QgsVectorLayer, cfg: PluginConfig, msag: MsagSettings) -> List[V.Issue]:
    """Issues for centerline sides the MSAG does not cover, and MSAG rows the map does not have."""
    src = resolve_layer(msag.layer_id, msag.layer_source)
    if src is None:
        raise ConfigError("Choose the MSAG extract layer.")
    missing = [label for label, value in (("street", msag.street_field), ("community", msag.community_field),
                                           ("low", msag.low_field), ("high", msag.high_field)) if not value]
    if missing:
        raise ConfigError("Choose the MSAG " + ", ".join(missing) + " field(s).")
    rows = _read_rows(src, msag)
    acc = FieldAccessor(layer, cfg)
    sides = _read_sides(layer, acc)
    return _compare(sides, rows)


def _read_rows(layer: QgsVectorLayer, msag: MsagSettings) -> List[dict]:
    fields = layer.fields()
    idx = {role: fields.lookupField(name) for role, name in (
        ("street", msag.street_field), ("community", msag.community_field),
        ("low", msag.low_field), ("high", msag.high_field), ("parity", msag.parity_field))}
    if any(idx[role] < 0 for role in ("street", "community", "low", "high")):
        raise ConfigError("A chosen MSAG field is not on that layer.")
    rows = []
    for feat in layer.getFeatures():
        street = loose_street_key(py_value(feat.attribute(idx["street"])) if idx["street"] >= 0 else "")
        community = clean(py_value(feat.attribute(idx["community"])) if idx["community"] >= 0 else "").upper()
        parity = normalize_parity(py_value(feat.attribute(idx["parity"])) if idx["parity"] >= 0 else None, BOTH)
        try:
            lo = int(float(py_value(feat.attribute(idx["low"]))))
            hi = int(float(py_value(feat.attribute(idx["high"]))))
        except (TypeError, ValueError):
            rows.append({"bad": True, "fid": feat.id(), "street": street, "community": community})
            continue
        rows.append({"bad": False, "fid": feat.id(), "street": street, "community": community,
                     "lo": min(lo, hi), "hi": max(lo, hi), "parity": parity or BOTH})
    return rows


def _read_sides(layer: QgsVectorLayer, acc: FieldAccessor) -> List[dict]:
    out = []
    for feat in layer.getFeatures():
        parts = {name: acc.get(feat, name) for name in STREET_NAME_FIELDS}
        if not name_key(parts):
            continue
        for side, comm_field, from_f, to_f, par_f in (
                ("L", "MSAGComm_L", "FromAddr_L", "ToAddr_L", "Parity_L"),
                ("R", "MSAGComm_R", "FromAddr_R", "ToAddr_R", "Parity_R")):
            fr, to = acc.get_int(feat, from_f), acc.get_int(feat, to_f)
            if fr is None or to is None or is_zero_range(fr, to):
                continue
            parity = normalize_parity(acc.get(feat, par_f), BOTH)
            if parity == ZERO:
                continue
            community = clean(acc.get(feat, comm_field) if acc.has(comm_field) else "").upper()
            out.append({"fid": feat.id(), "side": side, "street": name_key(parts), "community": community,
                        "lo": min(fr, to), "hi": max(fr, to), "parity": parity or BOTH})
    return out


def _covers(row, side) -> bool:
    return row["lo"] <= side["lo"] and row["hi"] >= side["hi"]


def _overlaps(row, side) -> bool:
    return side["lo"] <= row["hi"] and row["lo"] <= side["hi"]


def _parity_ok(row_parity, side_parity) -> bool:
    if row_parity in (None, BOTH) or side_parity in (None, BOTH):
        return True
    return row_parity == side_parity


def _compare(sides: Sequence[dict], rows: Sequence[dict]) -> List[V.Issue]:
    issues: List[V.Issue] = []
    good = [r for r in rows if not r.get("bad") and r["street"]]
    for row in rows:
        if row.get("bad"):
            issues.append(V.Issue(None, V.WARNING, "MSAG_BAD_ROW",
                                  f"MSAG feature {row['fid']} has no low and high range."))
    by_street: Dict[str, List[dict]] = defaultdict(list)
    for row in good:
        by_street[row["street"]].append(row)
    used = set()
    for side in sides:
        if not side["community"]:
            issues.append(V.Issue(side["fid"], V.WARNING, "MSAG_NO_COMMUNITY",
                                  f"{side['side']} has no MSAG community to compare.", side["side"]))
            continue
        same = [r for r in by_street.get(side["street"], []) if r["community"] == side["community"]]
        matched = [r for r in same if _parity_ok(r["parity"], side["parity"])]
        covered = [r for r in matched if _covers(r, side)]
        if covered:
            used.update(id(r) for r in covered)
            for r in matched:
                if _overlaps(r, side):
                    used.add(id(r))
            continue
        if matched:
            span = ", ".join(f"{r['lo']}–{r['hi']}" for r in matched[:3])
            issues.append(V.Issue(side["fid"], V.ERROR, "MSAG_RANGE",
                                  f"{side['side']} {side['lo']}–{side['hi']} is outside the MSAG range ({span}) "
                                  f"for {side['street']} in {side['community']}.", side["side"]))
            for r in matched:
                if _overlaps(r, side):
                    used.add(id(r))
        elif same:
            issues.append(V.Issue(side["fid"], V.ERROR, "MSAG_PARITY",
                                  f"{side['side']} parity {side['parity']} has no {side['parity']} MSAG row for "
                                  f"{side['street']} in {side['community']}.", side["side"]))
        elif by_street.get(side["street"]):
            issues.append(V.Issue(side["fid"], V.ERROR, "MSAG_COMMUNITY",
                                  f"{side['side']} community {side['community']} is not on the MSAG for "
                                  f"{side['street']}.", side["side"]))
        else:
            issues.append(V.Issue(side["fid"], V.ERROR, "MSAG_MISSING",
                                  f"{side['side']} {side['street']} in {side['community']} is not on the MSAG.",
                                  side["side"]))
    for row in good:
        if id(row) in used:
            continue
        hit = any(s["street"] == row["street"] and s["community"] == row["community"] and _overlaps(row, s)
                  and _parity_ok(row["parity"], s["parity"]) for s in sides)
        if hit:
            continue
        issues.append(V.Issue(None, V.WARNING, "MSAG_NOT_IN_GIS",
                              f"MSAG {row['street']} in {row['community']} {row['lo']}–{row['hi']} "
                              f"({row['parity']}) matches no centerline side. MSAG feature {row['fid']}."))
    return V.sort_issues(issues)
