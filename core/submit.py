# -*- coding: utf-8 -*-
"""Write the current NENA layers to a submission file, without the plugin's link fields."""

import os
from datetime import date

from qgis.core import (QgsFeature, QgsGeometry, QgsProject, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes)

from ..compat import py_value
from .layer_access import ConfigError
from .project_config import layer_for, resolve_layer
from .retire import is_current
from .tables import file_gdb_driver
from .zones import KINDS, ZoneLayer

# Plugin columns. NG9-1-1 does not carry them. Optional NENA fields stay.
CENTERLINE_OMIT = frozenset({
    "StreetID", "RoadStatus", "Hierarchy", "TravelTime", "Surface", "Overpass",
    "WhoEntered", "LastUpdatedBy",
})
ADDRESS_OMIT = frozenset({
    "StreetID", "RCL_NGUID", "Access_X", "Access_Y", "Addr_Side", "AccessPointID",
    "UnitType", "AddressStatus", "AddressType", "WhoEntered", "LastUpdatedBy",
})
ZONE_OMIT = frozenset({"WhoEntered", "LastUpdatedBy"})
_OMIT = {"centerline": CENTERLINE_OMIT, "address": ADDRESS_OMIT, "zone": ZONE_OMIT}


def kept_fields(role: str, names) -> list:
    omit = _OMIT.get(role, ZONE_OMIT)
    return [name for name in names if name not in omit]


def submission_target(path: str):
    """(path, driver, note). A .gdb request falls back to GeoPackage when QGIS cannot write one."""
    path = os.path.abspath(path)
    if path.lower().endswith(".gdb"):
        driver = file_gdb_driver()
        if driver:
            return path, driver, ""
        return path[:-4] + ".gpkg", "GPKG", "This QGIS cannot write a File Geodatabase, so this file is a GeoPackage."
    if not path.lower().endswith(".gpkg"):
        path += ".gpkg"
    return path, "GPKG", ""


def submission_sources(pcfg, centerline):
    """(role, table name, layer) for the centerline, address points, and zone layers."""
    out = []
    if centerline is not None:
        out.append(("centerline", "RoadCenterline", centerline))
    address = layer_for(pcfg.streets, "address") if pcfg is not None else None
    if address is not None:
        out.append(("address", "SiteStructureAddressPoint", address))
    if pcfg is not None:
        for raw in pcfg.zones.layers or []:
            zone = ZoneLayer.from_dict(raw)
            lyr = resolve_layer(zone.layer_id, zone.layer_source)
            if lyr is None:
                continue
            kind = KINDS.get(zone.kind)
            out.append(("zone", kind.table if kind else (zone.kind or "Zone"), lyr))
    return out


def _status_expire(feature, fields):
    status = expire = None
    for name in ("RoadStatus", "AddressStatus"):
        i = fields.lookupField(name)
        if i >= 0:
            status = py_value(feature.attribute(i))
            break
    i = fields.lookupField("Expire")
    if i >= 0:
        expire = py_value(feature.attribute(i))
    return status, expire


def _memory(layer, role, features):
    names = kept_fields(role, [f.name() for f in layer.fields()])
    indexes = []
    from qgis.core import QgsFields
    fields = QgsFields()
    src = layer.fields()
    for name in names:
        i = src.lookupField(name)
        if i < 0:
            continue
        fields.append(src.at(i))
        indexes.append(i)
    wkb = QgsWkbTypes.displayString(layer.wkbType())
    auth = layer.crs().authid() or "EPSG:4326"
    mem = QgsVectorLayer(f"{wkb}?crs={auth}", "submission", "memory")
    if not mem.isValid():
        raise ConfigError(f"Could not prepare '{layer.name()}' for export.")
    mem.dataProvider().addAttributes(fields.toList())
    mem.updateFields()
    batch = []
    for feat in features:
        nf = QgsFeature(mem.fields())
        nf.setAttributes([feat.attribute(i) for i in indexes])
        if feat.hasGeometry():
            nf.setGeometry(QgsGeometry(feat.geometry()))
        batch.append(nf)
    if batch:
        mem.dataProvider().addFeatures(batch)
    return mem


def _write(mem, path, table, driver, exists: bool):
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = driver
    opts.layerName = table
    opts.fileEncoding = "UTF-8"
    actions = getattr(QgsVectorFileWriter, "ActionOnExistingFile", QgsVectorFileWriter)
    opts.actionOnExistingFile = (actions.CreateOrOverwriteLayer if exists
                                 else actions.CreateOrOverwriteFile)
    res = QgsVectorFileWriter.writeAsVectorFormatV3(
        mem, path, QgsProject.instance().transformContext(), opts)
    err = res[0] if isinstance(res, tuple) else res
    no_error = getattr(QgsVectorFileWriter, "WriterError", QgsVectorFileWriter).NoError
    if err != no_error:
        detail = res[1] if isinstance(res, tuple) and len(res) > 1 else err
        raise ConfigError(f"Could not write '{table}': {detail}")


def export_submission(path: str, sources, today=None) -> dict:
    """Write current features. Retired and expired rows are left out."""
    if not sources:
        raise ConfigError("Choose the centerline and address layers first.")
    path, driver, note = submission_target(path)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        raise ConfigError(f"The folder '{folder}' does not exist.")
    today = today or date.today()
    written = {}
    used = set()
    exists = os.path.exists(path)
    for role, table, layer in sources:
        name = table
        n = 2
        while name.lower() in used:
            name = f"{table}_{n}"
            n += 1
        used.add(name.lower())
        keep = []
        fields = layer.fields()
        for feat in layer.getFeatures():
            status, expire = _status_expire(feat, fields)
            if is_current(status, expire, today):
                keep.append(feat)
        mem = _memory(layer, role, keep)
        try:
            _write(mem, path, name, driver, exists)
        finally:
            del mem
        exists = True
        written[name] = len(keep)
    total = sum(written.values())
    return {"path": path, "driver": driver, "note": note, "features": total, "layers": written}
