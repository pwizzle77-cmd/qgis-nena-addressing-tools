# -*- coding: utf-8 -*-
"""Copy features from an existing layer into a schema layer."""

from qgis.core import (Qgis, QgsCoordinateTransform, QgsFeature, QgsFeatureRequest, QgsFeatureSink,
                       QgsGeometry, QgsProject, QgsVectorDataProvider, QgsWkbTypes)

from ..compat import py_value
from .layer_access import ConfigError
from .schema_pack import convert_value

BATCH = 2000

_SKIP_FIELDS = {
    "fid", "ogc_fid", "objectid", "object_id", "gdb_geomattr_data",
    "shape", "shape_length", "shape_area", "shape_len",
}


def destination_fields(layer) -> list:
    """Attribute names import may fill. Primary keys and geometry housekeeping are left out."""
    pk = set()
    try:
        pk = set(layer.primaryKeyAttributes() or [])
    except Exception:  # noqa: BLE001
        pk = set()
    names = []
    for i, fld in enumerate(layer.fields()):
        if i in pk or fld.name().lower() in _SKIP_FIELDS:
            continue
        names.append(fld.name())
    return names


def source_field_names(layer) -> list:
    return [f.name() for f in layer.fields()]


def _spatial(layer) -> bool:
    if layer is None:
        return False
    try:
        if layer.wkbType() == Qgis.WkbType.NoGeometry:
            return False
    except Exception:  # noqa: BLE001
        return False
    null = getattr(Qgis.GeometryType, "Null", None)
    unknown = getattr(Qgis.GeometryType, "Unknown", None)
    return layer.geometryType() not in (null, unknown)


def _geom_label(layer) -> str:
    try:
        return QgsWkbTypes.displayString(layer.wkbType())
    except Exception:  # noqa: BLE001
        return "another geometry type"


def geometry_problem(source, dest) -> str:
    """Empty when a copy from source to dest is possible."""
    if source is None or dest is None:
        return ""
    if source.id() == dest.id():
        return "The source and the destination are the same layer."
    if _spatial(dest) and not _spatial(source):
        return f"'{source.name()}' has no geometry, so it cannot fill '{dest.name()}'."
    if _spatial(dest) and _spatial(source) and source.geometryType() != dest.geometryType():
        return (f"'{source.name()}' is {_geom_label(source)} and '{dest.name()}' is {_geom_label(dest)}. "
                f"Choose a {_geom_label(dest)} source.")
    return ""


def _can_add(layer) -> bool:
    provider = layer.dataProvider()
    if provider is None:
        return False
    enum = getattr(QgsVectorDataProvider, "Capability", QgsVectorDataProvider)
    flag = getattr(enum, "AddFeatures", None)
    if flag is None:
        return True
    return bool(provider.capabilities() & flag)


def _kind_of(field) -> str:
    name = (field.typeName() or "").lower()
    if any(k in name for k in ("date", "time")):
        return "datetime"
    if any(k in name for k in ("double", "real", "float", "numeric", "decimal")):
        return "double"
    if any(k in name for k in ("char", "text", "string", "varchar", "nvarchar")):
        return "string"
    if "int" in name or name in ("long", "short", "integer64", "smallint", "bigint"):
        return "int"
    try:
        from qgis.PyQt.QtCore import QMetaType
        kind = field.type()
        ints = {QMetaType.Type.Int, QMetaType.Type.Long, QMetaType.Type.LongLong,
                QMetaType.Type.UInt, QMetaType.Type.ULong, QMetaType.Type.ULongLong, QMetaType.Type.Short}
        floats = {QMetaType.Type.Double, QMetaType.Type.Float}
        dates = {QMetaType.Type.QDate, QMetaType.Type.QDateTime, QMetaType.Type.QTime}
        if kind in ints:
            return "int"
        if kind in floats:
            return "double"
        if kind in dates:
            return "datetime"
    except Exception:  # noqa: BLE001
        pass
    return "string"


def _street_name_blank(pairs, name_idx, feat) -> bool:
    """True when this row would create a master street with no street name."""
    src_i = next((si for di, si, _kind in pairs if di == name_idx), None)
    if src_i is None:
        return True
    return not str(_plain(feat.attribute(src_i)) or "").strip()


def _plain(value):
    value = py_value(value)
    if value is None:
        return None
    to_py = getattr(value, "toPyDateTime", None)
    if callable(to_py):
        try:
            return to_py()
        except Exception:  # noqa: BLE001
            to_s = getattr(value, "toString", None)
            if callable(to_s):
                return to_s("yyyy-MM-dd HH:mm:ss")
    to_date = getattr(value, "toPyDate", None)
    if callable(to_date):
        try:
            return to_date()
        except Exception:  # noqa: BLE001
            pass
    return value


def _transform(source, dest):
    if not _spatial(source) or not _spatial(dest):
        return None
    if not source.crs().isValid() or not dest.crs().isValid():
        return None
    if source.crs() == dest.crs():
        return None
    return QgsCoordinateTransform(source.crs(), dest.crs(), QgsProject.instance().transformContext())


def _transform_ok(code) -> bool:
    success = getattr(getattr(Qgis, "GeometryOperationResult", None), "Success", 0)
    return code in (0, success, None)


def _conform(geom: QgsGeometry, dest_wkb) -> bool:
    """Match single/multi. Returns True when a multi-part geometry was reduced to its first part."""
    reduced = False
    dest_multi = QgsWkbTypes.isMultiType(dest_wkb)
    src_multi = QgsWkbTypes.isMultiType(geom.wkbType())
    if not dest_multi and src_multi:
        body = geom.constGet()
        reduced = body is not None and body.numGeometries() > 1
        geom.convertToSingleType()
    elif dest_multi and not src_multi:
        geom.convertToMultiType()
    return reduced


def _fast_flag():
    enum = getattr(QgsFeatureSink, "Flag", QgsFeatureSink)
    return getattr(enum, "FastInsert", 0)


def _add(provider, batch):
    flag = _fast_flag()
    if flag:
        try:
            return provider.addFeatures(batch, flag)
        except TypeError:
            pass
    return provider.addFeatures(batch)


def _accepted(res, batch_len):
    if isinstance(res, tuple):
        ok = bool(res[0])
        feats = res[1] if len(res) > 1 else None
    else:
        ok, feats = bool(res), None
    if not ok:
        return False, 0
    if feats:
        return True, len(feats)
    return True, batch_len


def import_layer(source, dest, field_map, selected_only: bool = False, progress=None) -> dict:
    """
    Copy features into ``dest`` and save them immediately.

    ``field_map`` is destination field name -> source field name.
    """
    problem = geometry_problem(source, dest)
    if problem:
        raise ConfigError(problem)
    if dest.isEditable():
        if dest.isModified():
            raise ConfigError(f"Save or discard edits on '{dest.name()}' before importing.")
        dest.rollBack()
    if not _can_add(dest):
        raise ConfigError(f"'{dest.name()}' cannot accept new features.")

    spatial = _spatial(dest)
    pairs = []
    dest_fields = dest.fields()
    src_fields = source.fields()
    for dest_name, src_name in (field_map or {}).items():
        if not src_name:
            continue
        di = dest_fields.lookupField(dest_name)
        si = src_fields.lookupField(src_name)
        if di < 0 or si < 0:
            continue
        pairs.append((di, si, _kind_of(dest_fields.field(di))))
    if not pairs and not spatial:
        raise ConfigError(f"No fields were mapped onto '{dest.name()}'.")

    transform = _transform(source, dest)
    req = QgsFeatureRequest()
    total = source.featureCount()
    if selected_only:
        ids = list(source.selectedFeatureIds())
        if not ids:
            raise ConfigError(f"'{source.name()}' has no selected features.")
        req.setFilterFids(ids)
        total = len(ids)

    written = skipped = null_geom = bad_values = multipart = blank = 0
    seen = 0
    batch = []
    name_idx = dest_fields.lookupField("St_Name") if not spatial else -1

    def tick():
        if progress and total and total > 0 and (seen % 200 == 0 or seen == total):
            progress(100.0 * seen / max(total, 1))

    def flush():
        nonlocal written, batch
        if not batch:
            return
        ok, count = _accepted(_add(dest.dataProvider(), batch), len(batch))
        if not ok:
            raise ConfigError(
                f"Import into '{dest.name()}' stopped after {written} feature(s). "
                f"The destination rejected a batch. The features already accepted are saved.")
        written += count
        batch = []

    for feat in source.getFeatures(req):
        seen += 1
        if name_idx >= 0 and _street_name_blank(pairs, name_idx, feat):
            blank += 1
            tick()
            continue
        nf = QgsFeature(dest_fields)
        for di, si, kind in pairs:
            try:
                nf.setAttribute(di, convert_value(_plain(feat.attribute(si)), kind))
            except (TypeError, ValueError):
                nf.setAttribute(di, None)
                bad_values += 1
        if spatial:
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                null_geom += 1
                tick()
                continue
            g = QgsGeometry(geom)
            if transform is not None:
                if not _transform_ok(g.transform(transform)):
                    skipped += 1
                    tick()
                    continue
            if _conform(g, dest.wkbType()):
                multipart += 1
            nf.setGeometry(g)
        batch.append(nf)
        if len(batch) >= BATCH:
            flush()
        tick()
    flush()
    dest.updateExtents()
    try:
        dest.reload()
    except Exception:  # noqa: BLE001 - the features are already stored
        pass
    dest.triggerRepaint()
    if progress:
        progress(100)
    return {
        "written": written,
        "skipped": skipped,
        "blank": blank,
        "null_geometry": null_geom,
        "bad_values": bad_values,
        "multipart": multipart,
        "mapped": len(pairs),
    }
