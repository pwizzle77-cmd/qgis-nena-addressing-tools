# -*- coding: utf-8 -*-
"""
Calculated fields, written just before a road or address layer saves.

Road centerlines:

* **FullName**     – the NENA name parts in order ("North Main Street").
* **LSt_FullName** – the legacy parts in order ("N MAIN ST").
* **TravelTime**   – minutes to drive the segment at SpeedLimit (mph), from
  its measured length. Empty when SpeedLimit is empty or zero, so a missing
  speed shows up instead of being guessed.
* **Hierarchy**    – filled from RoadClass only when it is empty.

Address points:

* **FullName** – the name parts in order.
* **FullAddr** – "123A North Main Street Apt 4": number prefix, number and
  suffix, full road name, unit type and unit. With no number, a landmark or
  milepost leads the text.

The master street name cascade (rename, sync, assign) writes FullName on
roads and addresses and FullAddr on addresses straight away; the save
recalculates them for any other change. ``recalculate`` fills them for every
existing feature. The text functions here are plain Python.
"""

from typing import Dict, Iterable, Optional

from .streets.names import NAME_PARTS, clean

LEGACY_PARTS = ("LSt_PreDir", "LSt_Name", "LSt_Typ", "LSt_PosDir")
METRES_PER_MILE = 1609.344

# RoadClass (NENA registry) -> routing hierarchy, used only when Hierarchy is empty.
HIERARCHY_BY_CLASS = {
    "PRIMARY": 2, "SECONDARY": 3, "LOCAL": 6, "RAMP": 2, "SERVICE DRIVE": 7, "VEHICULAR TRAIL": 7,
    "ALLEY": 7, "PRIVATE": 7, "PARKING LOT": 7, "OTHER": 6,
}
NOT_DRIVEN = {"WALKWAY", "STAIRWAY", "TRAIL", "BRIDLE PATH"}

CENTERLINE_FIELDS = ("FullName", "LSt_FullName", "TravelTime", "Hierarchy")
ADDRESS_FIELDS = ("FullName", "FullAddr")


def _get(values: dict, name: str):
    if name in values:
        return values[name]
    lower = name.lower()
    for key, value in values.items():
        if str(key).lower() == lower:
            return value
    return None


def road_full_name(values: dict) -> str:
    return " ".join(clean(_get(values, p)) for p in NAME_PARTS if clean(_get(values, p)))


def legacy_full_name(values: dict) -> str:
    return " ".join(clean(_get(values, p)) for p in LEGACY_PARTS if clean(_get(values, p)))


def _number_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean(value)


def full_address(values: dict, full_name: Optional[str] = None) -> str:
    """'123A North Main Street Apt 4'. A one-letter suffix joins the number; '1/2' gets a space."""
    number = _number_text(_get(values, "Add_Number"))
    suffix = clean(_get(values, "AddNum_Suf"))
    prefix = clean(_get(values, "AddNum_Pre"))
    if number and suffix:
        number = number + suffix if (len(suffix) == 1 and suffix.isalpha()) else f"{number} {suffix}"
    elif suffix:
        number = suffix
    if prefix and number:
        number = prefix + number          # grid prefixes are written against the number: N1234, A-12
    name = clean(full_name) if full_name is not None else ""
    if not name:
        name = clean(_get(values, "FullName")) or road_full_name(values)
    unit = clean(_get(values, "Unit"))
    unit_type = clean(_get(values, "UnitType"))
    unit_text = ""
    if unit:
        unit_text = f"{unit_type} {unit}" if unit_type and not unit.upper().startswith(unit_type.upper()) else unit
    if not number:
        landmark = clean(_get(values, "LandmkName"))
        mile = clean(_get(values, "Mile_Post"))
        number = landmark or (f"Milepost {mile}" if mile else "")
    return " ".join(part for part in (number, name, unit_text) if part)


def travel_minutes(length_m, speed_mph) -> Optional[float]:
    """Minutes to cover ``length_m`` metres at ``speed_mph``. None without a usable speed or length."""
    try:
        speed = float(speed_mph)
        length = float(length_m)
    except (TypeError, ValueError):
        return None
    if speed <= 0 or length < 0:
        return None
    return round(length / (speed * METRES_PER_MILE) * 60.0, 4)


def hierarchy_for(road_class) -> Optional[int]:
    text = clean(road_class).upper()
    if not text or text in NOT_DRIVEN:
        return None
    return HIERARCHY_BY_CLASS.get(text)


def _same(a, b) -> bool:
    if a in (None, "") and b in (None, ""):
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-6
    return str(a) == str(b)


def centerline_changes(values: dict, length_m: Optional[float], have: Iterable[str]) -> Dict[str, object]:
    """Calculated road fields that differ from ``values``. ``have`` are the NENA fields the layer has."""
    have = set(have)
    out: Dict[str, object] = {}
    if "FullName" in have:
        full = road_full_name(values) or None
        if not _same(full, _get(values, "FullName")):
            out["FullName"] = full
    if "LSt_FullName" in have:
        legacy = legacy_full_name(values) or None
        if not _same(legacy, _get(values, "LSt_FullName")):
            out["LSt_FullName"] = legacy
    if "TravelTime" in have and length_m is not None:
        minutes = travel_minutes(length_m, _get(values, "SpeedLimit"))
        if not _same(minutes, _get(values, "TravelTime")):
            out["TravelTime"] = minutes
    if "Hierarchy" in have and _get(values, "Hierarchy") in (None, ""):
        level = hierarchy_for(_get(values, "RoadClass"))
        if level is not None:
            out["Hierarchy"] = level
    return out


def address_changes(values: dict, have: Iterable[str]) -> Dict[str, object]:
    """Calculated address fields that differ from ``values``."""
    have = set(have)
    out: Dict[str, object] = {}
    full = road_full_name(values) or None
    if "FullName" in have and full and not _same(full, _get(values, "FullName")):
        out["FullName"] = full
    if "FullAddr" in have:
        merged = dict(values)
        merged.update(out)
        text = full_address(merged, merged.get("FullName") if "FullName" in out else None) or None
        if not _same(text, _get(values, "FullAddr")):
            out["FullAddr"] = text
    return out


# ---------------------------------------------------------------------------
# QGIS
# ---------------------------------------------------------------------------
def _accessor_values(acc, feat, names) -> dict:
    return {name: acc.get(feat, name) for name in names if acc.has(name)}


_ROAD_INPUTS = tuple(NAME_PARTS) + LEGACY_PARTS + ("SpeedLimit", "RoadClass") + CENTERLINE_FIELDS
_ADDRESS_INPUTS = tuple(NAME_PARTS) + ("AddNum_Pre", "Add_Number", "AddNum_Suf", "Unit", "UnitType") + ADDRESS_FIELDS


def centerline_accessor(layer):
    """FieldAccessor for a road layer: its stored mapping, plus the calculated fields found by name."""
    from .config import PluginConfig, load_from_layer
    from .layer_access import FieldAccessor
    from .schema import auto_map
    cfg = load_from_layer(layer) or PluginConfig(mapping=auto_map([f.name() for f in layer.fields()]))
    mapping = dict(cfg.mapping)
    found = auto_map([f.name() for f in layer.fields()])
    for name in _ROAD_INPUTS:
        if not mapping.get(name) and found.get(name):
            mapping[name] = found[name]
    cfg.mapping = mapping
    return FieldAccessor(layer, cfg)


def address_accessor(layer, pcfg):
    from .addressing.engine import address_mapping
    from .config import PluginConfig
    from .layer_access import FieldAccessor
    cfg = PluginConfig()
    cfg.mapping = address_mapping(layer, pcfg)
    return FieldAccessor(layer, cfg)


def _length_m(geom, da) -> Optional[float]:
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    try:
        from qgis.core import Qgis
        raw = da.measureLength(geom)
        return float(da.convertLengthMeasurement(raw, Qgis.DistanceUnit.Meters))
    except Exception:  # noqa: BLE001
        return None


def feature_changes(role: str, acc, feat, da=None) -> Dict[str, object]:
    """Calculated values for one feature (NENA names -> new value)."""
    if role == "centerline":
        values = _accessor_values(acc, feat, _ROAD_INPUTS)
        length = _length_m(feat.geometry(), da) if (da is not None and acc.has("TravelTime")
                                                    and feat.hasGeometry()) else None
        return centerline_changes(values, length, [n for n in CENTERLINE_FIELDS if acc.has(n)])
    values = _accessor_values(acc, feat, _ADDRESS_INPUTS)
    return address_changes(values, [n for n in ADDRESS_FIELDS if acc.has(n)])


def stamp_buffer(role: str, layer, acc) -> int:
    """Write calculated values for the added and changed features in the edit buffer. Returns the count."""
    from . import geometry_utils as gu
    from .editor_user import _feature, _ids
    buffer = layer.editBuffer() if layer is not None else None
    if buffer is None or not layer.isModified():
        return 0
    if not any(acc.has(n) for n in (CENTERLINE_FIELDS if role == "centerline" else ADDRESS_FIELDS)):
        return 0
    deleted = _ids(buffer.deletedFeatureIds())
    fids = (_ids(buffer.addedFeatures()) | _ids(buffer.changedAttributeValues())) - deleted
    if hasattr(buffer, "changedGeometries"):
        fids |= _ids(buffer.changedGeometries()) - deleted
    da = gu.make_distance_area(layer.crs()) if role == "centerline" else None
    count = 0
    for fid in fids:
        feat = _feature(layer, fid)
        if feat is None:
            continue
        changes = feature_changes(role, acc, feat, da)
        attrs = {acc.index[k]: acc.coerce(k, v) for k, v in changes.items() if k in acc.index}
        if attrs and layer.changeAttributeValues(fid, attrs):
            count += 1
    return count


def recalculate(role: str, layer, acc, command: str = "NENA: recalculate fields") -> int:
    """Fill the calculated fields of every feature. One undo step; save the layer to keep it."""
    from . import geometry_utils as gu
    from .layer_access import apply_changes
    da = gu.make_distance_area(layer.crs()) if role == "centerline" else None
    changes = {}
    for feat in layer.getFeatures():
        diff = feature_changes(role, acc, feat, da)
        if diff:
            changes[feat.id()] = diff
    return apply_changes(layer, acc, changes, command)


class DerivedFieldsWatcher:
    """Calculate FullName, LSt_FullName, TravelTime, Hierarchy, and FullAddr just before a layer saves."""

    def __init__(self, notify):
        from qgis.core import QgsProject
        self.notify = notify
        self._conns = []
        self._layers = {}
        project = QgsProject.instance()
        project.readProject.connect(self.refresh)
        project.layersAdded.connect(self.refresh)
        project.layersRemoved.connect(self.refresh)
        self.refresh()

    def unload(self):
        from qgis.core import QgsProject
        from .qt_safety import safe_disconnect
        project = QgsProject.instance()
        for signal in (project.readProject, project.layersAdded, project.layersRemoved):
            safe_disconnect(project, signal, self.refresh)
        self._detach()

    def _detach(self):
        from .qt_safety import disconnect_all
        disconnect_all(self._conns)
        self._conns = []
        self._layers = {}

    def refresh(self, *_args):
        import functools
        from qgis.core import QgsProject, QgsVectorLayer
        from .config import load_from_layer
        from .project_config import layer_for, load_project_config
        self._detach()
        pcfg = load_project_config()
        found = {}
        rcl = layer_for(pcfg.streets, "centerline")
        if rcl is not None:
            found[rcl.id()] = ("centerline", rcl)
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.id() not in found and load_from_layer(layer) is not None:
                found[layer.id()] = ("centerline", layer)
        addr = layer_for(pcfg.streets, "address")
        if addr is not None:
            found[addr.id()] = ("address", addr)
        self._layers = found
        for layer_id, (_role, layer) in found.items():
            slot = functools.partial(self._before, layer_id)
            layer.beforeCommitChanges.connect(slot)
            self._conns.append((layer, layer.beforeCommitChanges, slot))

    def _before(self, layer_id, *_args):
        item = self._layers.get(layer_id)
        if item is None:
            return
        role, layer = item
        try:
            if role == "centerline":
                acc = centerline_accessor(layer)
            else:
                from .project_config import load_project_config
                acc = address_accessor(layer, load_project_config())
            stamp_buffer(role, layer, acc)
        except Exception as exc:  # noqa: BLE001 - a calculated field must never block the save
            self.notify("Warning", f"Calculated fields were not updated: {exc}")
