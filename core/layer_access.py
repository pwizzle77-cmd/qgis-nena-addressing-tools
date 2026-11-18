# -*- coding: utf-8 -*-
"""
Reading a QGIS line layer into plain records, and writing values back
through the edit buffer (so everything is undoable and non-destructive
until the user saves).
"""

import contextlib
from datetime import datetime
from typing import Dict, Hashable, Iterable, List, Optional

from qgis.core import QgsFeatureRequest, QgsVectorLayer
from qgis.PyQt.QtCore import QDate, QDateTime

from ..compat import py_value, make_field
from . import geometry_utils as gu
from .config import PluginConfig
from .ranging import normalize_parity
from .schema import SIDE_FIELDS, get_def
from .topology import SegmentInfo


class ConfigError(Exception):
    """Raised for user-fixable configuration problems (missing mapping etc.)."""


def _stores_fraction(type_name: str) -> bool:
    """True when a numeric field keeps a fractional value (float, double, decimal, …)."""
    name = (type_name or "").lower()
    return any(token in name for token in ("float", "real", "double", "decimal", "numeric", "money"))


class FieldAccessor:
    """Resolves NENA logical names to layer field indexes and converts values."""

    def __init__(self, layer: QgsVectorLayer, cfg: PluginConfig):
        self.layer = layer
        self.cfg = cfg
        self.fields = layer.fields()
        self.index: Dict[str, int] = {}
        for nena, lname in cfg.mapping.items():
            if not lname:
                continue
            i = self.fields.lookupField(lname)
            if i >= 0:
                self.index[nena] = i

    # -- lookups ---------------------------------------------------------
    def has(self, nena: str) -> bool:
        return nena in self.index

    def missing(self, names: Iterable[str]) -> List[str]:
        return [n for n in names if n not in self.index]

    def require(self, names: Iterable[str]) -> None:
        miss = self.missing(names)
        if miss:
            raise ConfigError("These NENA fields are not mapped to layer fields: " + ", ".join(miss) +
                              ". Map them in the Field Mapping tab (or add the fields).")

    def get(self, feature, nena: str):
        i = self.index.get(nena)
        if i is None:
            return None
        return py_value(feature.attribute(i))

    def get_int(self, feature, nena: str) -> Optional[int]:
        v = self.get(feature, nena)
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def street_key(self, feature) -> Optional[str]:
        parts = []
        for nena in self.cfg.street_key_fields:
            v = self.get(feature, nena)
            if v is not None and str(v).strip():
                parts.append(str(v).strip())
        if not parts:
            return None
        return " ".join(" ".join(parts).upper().split())

    def side_range(self, feature, side: str):
        names = SIDE_FIELDS[side]
        return (self.get_int(feature, names["from"]), self.get_int(feature, names["to"]),
                self.get(feature, names["parity"]))

    # -- writing ---------------------------------------------------------
    def coerce(self, nena: str, value):
        """Convert a Python value to something the mapped field accepts."""
        i = self.index[nena]
        fld = self.fields.at(i)
        if value is None:
            return None
        tname = (fld.typeName() or "").lower()
        if isinstance(value, datetime):
            if "datetime" in tname or "timestamp" in tname:
                return QDateTime(value)
            if tname == "date":
                return QDate(value.year, value.month, value.day)
            return value.isoformat(timespec="seconds")
        if fld.isNumeric():
            try:
                # SQL Server reports float columns as "float", not "double" or "real".
                # Treating that as a whole number saved longitude -82.65 as -82.
                if _stores_fraction(tname):
                    return float(value)
                return int(value)
            except (TypeError, ValueError):
                return None
        return str(value)


# ---------------------------------------------------------------------------
def read_segments(layer: QgsVectorLayer, acc: FieldAccessor, fids: Optional[Iterable[Hashable]] = None,
                  progress=None) -> Dict[Hashable, SegmentInfo]:
    """
    Read features into SegmentInfo records (start/end/bearings/length/key).
    ``extra`` holds: 'L'/'R' -> (from, to, parity), 'disjoint', 'empty', 'parts'.
    """
    da = gu.make_distance_area(layer.crs())
    if fids is None:
        it = layer.getFeatures()
        total = layer.featureCount() or 1
    else:
        fid_list = list(fids)
        it = layer.getFeatures(QgsFeatureRequest().setFilterFids(fid_list))
        total = len(fid_list) or 1
    out: Dict[Hashable, SegmentInfo] = {}
    for n, f in enumerate(it):
        geom = f.geometry()
        info = gu.line_info(geom)
        seg = SegmentInfo(
            fid=f.id(),
            start=info.start or (0.0, 0.0),
            end=info.end or (0.0, 0.0),
            length=gu.length_m(da, geom) if not info.empty else 0.0,
            key=acc.street_key(f),
            start_bearing=info.start_bearing,
            end_bearing=info.end_bearing,
        )
        seg.extra["L"] = acc.side_range(f, "L")
        seg.extra["R"] = acc.side_range(f, "R")
        seg.extra["empty"] = info.empty
        seg.extra["disjoint"] = info.disjoint
        seg.extra["parts"] = info.parts
        out[f.id()] = seg
        if progress and n % 200 == 0:
            progress(int(100 * n / total))
    return out


def ensure_editable(layer: QgsVectorLayer) -> bool:
    """Start an edit session if needed. Returns True if we started it."""
    if layer.isEditable():
        return False
    if not layer.startEditing():
        raise ConfigError(f"Layer '{layer.name()}' cannot be edited (read-only data source?).")
    return True


def apply_changes(layer: QgsVectorLayer, acc: FieldAccessor, changes: Dict[Hashable, Dict[str, object]],
                  command: str) -> int:
    """
    Write {fid: {nena_name: value}} in ONE undoable edit command.
    Values equal to the current value are skipped. Returns the number of
    features that actually changed.
    """
    if not changes:
        return 0
    ensure_editable(layer)
    layer.beginEditCommand(command)
    changed = 0
    try:
        request = QgsFeatureRequest().setFilterFids(list(changes.keys()))
        for f in layer.getFeatures(request):
            vals = changes.get(f.id(), {})
            new_attrs, old_attrs = {}, {}
            for nena, value in vals.items():
                if nena not in acc.index:
                    continue
                idx = acc.index[nena]
                new = acc.coerce(nena, value)
                old = py_value(f.attribute(idx))
                if _same(old, new):
                    continue
                new_attrs[idx] = new
                old_attrs[idx] = f.attribute(idx)
            if new_attrs:
                layer.changeAttributeValues(f.id(), new_attrs, old_attrs)
                changed += 1
    except Exception:
        layer.destroyEditCommand()
        raise
    if changed:
        layer.endEditCommand()
    else:
        layer.destroyEditCommand()
    return changed


def _same(old, new) -> bool:
    if old is None and new is None:
        return True
    try:
        return py_value(old) == py_value(new)
    except Exception:  # pragma: no cover - exotic types
        return False


def side_values(side: str, rng, write_parity: bool) -> Dict[str, object]:
    """Turn a (from, to, parity) tuple into {nena_name: value}."""
    names = SIDE_FIELDS[side]
    out = {names["from"]: int(rng[0]), names["to"]: int(rng[1])}
    if write_parity:
        out[names["parity"]] = normalize_parity(rng[2], "B")
    return out


_FULL_NAME_ALIASES = ("FullName", "FULLNAME", "FULL_NAME", "FULL_STREET", "STREET_FULL",
                      "FULLROAD", "FULL_ROAD", "FULLROADNAME", "FULL_ROAD_NAME")


def ensure_full_name_mapping(layer: QgsVectorLayer, acc: FieldAccessor) -> FieldAccessor:
    """
    Map the address layer's full road name, adding a FullName column when none of the
    known names is present. A provider that refuses the column leaves the accessor unchanged.
    """
    if acc.has("FullName"):
        return acc
    fields = layer.fields()
    physical = None
    for name in _FULL_NAME_ALIASES:
        idx = fields.lookupField(name)
        if idx >= 0:
            physical = fields.at(idx).name()
            break
    if physical is None:
        from ..compat import make_field
        started = False
        try:
            ensure_editable(layer)
            layer.beginEditCommand("NENA: add full road name")
            started = True
            if not layer.addAttribute(make_field("FullName", "string", 254)):
                layer.destroyEditCommand()
                return acc
            layer.endEditCommand()
        except Exception:  # noqa: BLE001 - addressing still proceeds without the column
            if started:
                with contextlib.suppress(Exception):
                    layer.destroyEditCommand()
            return acc
        layer.updateFields()
        idx = layer.fields().lookupField("FullName")
        if idx < 0:
            return acc
        physical = layer.fields().at(idx).name()
    cfg = acc.cfg
    cfg.mapping = dict(cfg.mapping)
    cfg.mapping["FullName"] = physical
    mapped = FieldAccessor(layer, cfg)
    _backfill_empty_full_names(layer, mapped)
    return mapped


def _backfill_empty_full_names(layer: QgsVectorLayer, acc: FieldAccessor) -> None:
    """Fill FullName from the name parts already stored on the address."""
    from .streets.names import NAME_PARTS, clean, full_name
    if not acc.has("FullName"):
        return
    changes = {}
    for feat in layer.getFeatures():
        if clean(acc.get(feat, "FullName")):
            continue
        name = full_name({part: acc.get(feat, part) for part in NAME_PARTS})
        if name:
            changes[feat.id()] = {"FullName": name}
    if changes:
        apply_changes(layer, acc, changes, "NENA: fill full road name")


def add_missing_fields(layer: QgsVectorLayer, cfg: PluginConfig, names: Iterable[str]) -> List[str]:
    """
    Add fields that don't exist on the layer, using the field template when
    one is loaded (its names, types and widths) or the built-in NENA schema,
    and map them. Requires a provider that supports adding attributes.
    Returns the physical names added.
    """
    from .template import effective_fields
    defs = {d.name: d for d in effective_fields(cfg.template)}
    fields = layer.fields()
    to_add, logical_for = [], {}
    for n in names:
        if cfg.mapping.get(n):
            continue
        d = defs.get(n) or get_def(n)
        if d is None:
            continue
        for candidate in (d.physical_name, d.name):
            idx = fields.lookupField(candidate)
            if idx >= 0:
                cfg.mapping[n] = fields.at(idx).name()
                break
        else:
            fld = make_field(d.physical_name, d.ftype, d.length)
            to_add.append(fld)
            logical_for[fld.name()] = n
    if not to_add:
        return []
    started = ensure_editable(layer)
    layer.beginEditCommand("NENA: add missing fields")
    ok = all(layer.addAttribute(f) for f in to_add)
    if not ok:
        layer.destroyEditCommand()
        if started:
            layer.rollBack()
        raise ConfigError("The data provider refused to add one or more fields "
                          "(shapefiles also limit field names to 10 characters).")
    layer.endEditCommand()
    layer.updateFields()
    for f in to_add:
        cfg.mapping[logical_for[f.name()]] = f.name()
    return [f.name() for f in to_add]
