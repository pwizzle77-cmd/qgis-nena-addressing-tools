# -*- coding: utf-8 -*-
"""
Reverse the direction of road centerlines without breaking their attributes.

A NENA centerline is read in the direction it was drawn: From is at the first
vertex, To at the last, and left/right are as seen walking that way. Flipping
only the geometry would put every house on the wrong side and run every range
backwards. Reversing here also:

* swaps the left and right values (ESN_L <-> ESN_R, Parity_L <-> Parity_R, …),
* moves the ranges so each side keeps its own numbers, now running the new way
  (new FromAddr_L = old ToAddr_R, new ToAddr_L = old FromAddr_R, and so on),
* flips one-way (FT <-> TF),
* flips the side stored on the address points and access points that hang off
  the reversed segment (L <-> R), matched by RCL_NGUID.

The houses keep their numbers and stay on the same physical side of the road.
If the ranges themselves were wrong because the line was backwards, run Range
on the reversed segments afterwards.
"""

from typing import Dict, Iterable, Optional, Sequence

RANGE_SWAP = {
    "FromAddr_L": "ToAddr_R",
    "ToAddr_L": "FromAddr_R",
    "FromAddr_R": "ToAddr_L",
    "ToAddr_R": "FromAddr_L",
}
ONE_WAY_FLIP = {"FT": "TF", "TF": "FT"}
SIDE_FLIP = {"L": "R", "R": "L"}


def reversal_values(values: Dict[str, object]) -> Dict[str, object]:
    """
    New attribute values for a reversed centerline.

    ``values`` maps NENA names to the current values of one feature. Only names
    whose value changes are returned.
    """
    out = {}
    for name, current in values.items():
        if name in RANGE_SWAP:
            if RANGE_SWAP[name] in values:
                new = values[RANGE_SWAP[name]]
            else:
                continue
        elif name.endswith("_L") and name[:-2] + "_R" in values:
            new = values[name[:-2] + "_R"]
        elif name.endswith("_R") and name[:-2] + "_L" in values:
            new = values[name[:-2] + "_L"]
        elif name == "OneWay" and current is not None:
            new = ONE_WAY_FLIP.get(str(current).strip().upper(), current)
        else:
            continue
        if new != current:
            out[name] = new
    return out


ONE_WAY_CHOICES = (
    ("B", "Two-way (B)", "Traffic can travel both ways"),
    ("FT", "One-way From → To (FT)", "Traffic travels only in the direction the line was drawn"),
    ("TF", "One-way To → From (TF)", "Traffic travels only against the direction the line was drawn"),
)


def normalize_one_way(value) -> Optional[str]:
    """'ft' -> 'FT'. Anything that is not B, FT, or TF becomes None."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return text if text in ("B", "FT", "TF") else None


def flip_side(value) -> Optional[str]:
    """'L' -> 'R', 'R' -> 'L'. Anything else is returned unchanged."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return SIDE_FLIP.get(text, value)


# ---------------------------------------------------------------------------
# QGIS
# ---------------------------------------------------------------------------
def reversed_geometry(geom):
    """The same line drawn the other way. Multi-part lines also reverse their part order."""
    from qgis.core import QgsGeometry
    if geom is None or geom.isNull() or geom.isEmpty():
        return geom
    curve = geom.constGet()
    if geom.isMultipart():
        out = curve.createEmptyWithSameType()
        for index in reversed(range(curve.numGeometries())):
            out.addGeometry(curve.geometryN(index).reversed())
        return QgsGeometry(out)
    return QgsGeometry(curve.reversed())


def reverse_centerlines(layer, cfg, fids: Iterable[int], linked: Sequence[tuple] = (),
                        parity_defaults: Optional[Dict[str, str]] = None) -> dict:
    """
    Reverse the given centerlines in one undo step on each layer.

    ``linked`` holds ``(layer, side_field, nguid_field)`` for the address-point
    and access-point layers whose side should follow. With ``parity_defaults``
    (``{"L": "O", "R": "E"}``) the parity of each side is set to the usual
    convention for the new direction instead of travelling with the old side
    (a side marked Z stays Z); use this before re-ranging. Returns counts.
    """
    from qgis.core import QgsFeatureRequest
    from .layer_access import ConfigError, FieldAccessor, ensure_editable

    fids = list(fids)
    if not fids:
        raise ConfigError("Select the centerlines to reverse first.")
    acc = FieldAccessor(layer, cfg)
    ensure_editable(layer)
    nguids = set()
    reversed_count = 0
    layer.beginEditCommand("NENA: reverse direction")
    try:
        for feature in layer.getFeatures(QgsFeatureRequest().setFilterFids(fids)):
            geom = reversed_geometry(feature.geometry())
            if geom is None or geom.isNull():
                continue
            layer.changeGeometry(feature.id(), geom)
            values = {name: acc.get(feature, name) for name in acc.index}
            changes = reversal_values(values)
            if parity_defaults:
                after = dict(values, **changes)
                for side in ("L", "R"):
                    name = f"Parity_{side}"
                    if name not in acc.index:
                        continue
                    if str(after.get(name) or "").strip().upper() == "Z":
                        continue
                    wanted = parity_defaults.get(side)
                    if wanted and wanted != values.get(name):
                        changes[name] = wanted
                    elif wanted == values.get(name):
                        changes.pop(name, None)
            if changes:
                attrs = {acc.index[name]: acc.coerce(name, value) for name, value in changes.items()}
                layer.changeAttributeValues(feature.id(), attrs)
            nguid = values.get("RCL_NGUID")
            if nguid:
                nguids.add(str(nguid))
            reversed_count += 1
    except Exception:
        layer.destroyEditCommand()
        raise
    layer.endEditCommand()

    flipped = 0
    for other, side_field, nguid_field in linked:
        if other is None or not nguids:
            continue
        side_index = other.fields().indexFromName(side_field)
        link_index = other.fields().indexFromName(nguid_field)
        if side_index < 0 or link_index < 0:
            continue
        changes = {}
        for feature in other.getFeatures():
            link = feature[link_index]
            if link is None or str(link) not in nguids:
                continue
            old = feature[side_index]
            new = flip_side(old)
            if new != old:
                changes[feature.id()] = new
        if not changes:
            continue
        ensure_editable(other)
        other.beginEditCommand("NENA: side follows reversed road")
        for fid, value in changes.items():
            other.changeAttributeValue(fid, side_index, value)
        other.endEditCommand()
        flipped += len(changes)
    return {"lines": reversed_count, "linked": flipped}



def set_one_way(layer, cfg, fids: Iterable[int], value: Optional[str]) -> int:
    """
    Write OneWay on the given centerlines in one undo step. Returns how many changed.

    ``value`` is B, FT, TF, or None to clear it. The line and its address
    ranges are not touched. FT and TF are read against the direction the line
    was drawn, so Reverse flips them.
    """
    from qgis.core import QgsFeatureRequest
    from .layer_access import ConfigError, FieldAccessor, ensure_editable

    fids = list(fids)
    if not fids:
        raise ConfigError("Select the centerlines to mark first.")
    acc = FieldAccessor(layer, cfg)
    if not acc.has("OneWay"):
        raise ConfigError("NO_ONEWAY_FIELD")
    wanted = normalize_one_way(value) if value else None
    ensure_editable(layer)
    index = acc.index["OneWay"]
    changed = 0
    layer.beginEditCommand("NENA: set one-way")
    try:
        for feature in layer.getFeatures(QgsFeatureRequest().setFilterFids(fids)):
            raw = acc.get(feature, "OneWay")
            current = None if raw is None else str(raw).strip()
            if (current or None) == wanted:
                continue
            layer.changeAttributeValue(feature.id(), index, acc.coerce("OneWay", wanted))
            changed += 1
    except Exception:
        layer.destroyEditCommand()
        raise
    layer.endEditCommand()
    return changed
