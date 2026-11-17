# -*- coding: utf-8 -*-
"""
Write saved edits into the version tables.

The live layer stays the current record. On save, each added feature is
stored as a new version, each update is stored as the feature after the
change (with the old and new values), and each delete is stored as the
removed feature. The version row is written after the live commit returns,
so the live save is already stored and the file lock from that commit has
been released. A failed version write does not roll back the live save.
"""

import os
from datetime import datetime

from qgis.core import QgsFeature, QgsFeatureRequest, QgsGeometry, QgsProject
from qgis.PyQt.QtCore import QObject, QTimer

from ..compat import make_field, py_value
from . import archive_model as M
from .config import load_from_layer
from .importer import BATCH, _SKIP_FIELDS, _accepted, _add, _conform, _kind_of, _spatial
from .layer_access import ConfigError
from .editor_user import connection_login
from .project_config import layer_for, load_project_config
from .schema_pack import ARCHIVE_COLUMNS, convert_value
from .qt_safety import disconnect_all, safe_disconnect

_KEY_FIELDS = {
    "centerline": ("RCL_NGUID",),
    "streets": ("StreetID",),
    "address": ("Site_NGUID",),
    "driveway": ("DW_NGUID",),
    "access": ("AccessPointID",),
}


def pairing(pcfg):
    """``{role: live layer or None}`` and ``{role: version layer or None}``."""
    live = {
        "centerline": layer_for(pcfg.streets, "centerline"),
        "streets": layer_for(pcfg.streets, "master"),
        "address": layer_for(pcfg.streets, "address"),
        "driveway": layer_for(pcfg.addresses, "driveway"),
        "access": layer_for(pcfg.addresses, "access"),
    }
    archive = {role: layer_for(pcfg.archive, role) for role in live}
    return live, archive


def current_user() -> str:
    name = ""
    try:
        from qgis.core import QgsApplication
        name = QgsApplication.userLoginName() or QgsApplication.userFullName() or ""
    except Exception:  # noqa: BLE001
        name = ""
    if not name:
        name = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    return str(name)[:64]


def key_fields_for(layer, role: str, pcfg) -> list:
    names = []
    if role == "centerline" and layer is not None:
        cfg = load_from_layer(layer)
        mapped = cfg.mapping.get("RCL_NGUID") if cfg is not None else ""
        if mapped:
            names.append(mapped)
    if role == "streets" and pcfg is not None:
        link = (pcfg.streets.street_id_field or "").strip()
        if link:
            names.append(link)
    for name in _KEY_FIELDS.get(role, ()):
        if name not in names:
            names.append(name)
    return names


def _mapping(value) -> dict:
    if not value:
        return {}
    try:
        return dict(value)
    except Exception:  # noqa: BLE001
        return {}


def _id_set(value) -> set:
    if not value:
        return set()
    try:
        return set(value.keys())
    except Exception:  # noqa: BLE001
        try:
            return set(value)
        except Exception:  # noqa: BLE001
            return set()


def _values(feat) -> dict:
    out = {}
    fields = feat.fields()
    for i in range(fields.count()):
        name = fields.at(i).name()
        if name.lower() in _SKIP_FIELDS:
            continue
        out[name] = py_value(feat.attribute(i))
    return out


def _clone_geom(geom):
    if geom is None:
        return None
    try:
        if geom.isNull() or geom.isEmpty():
            return None
    except Exception:  # noqa: BLE001
        return None
    return QgsGeometry(geom)


def _provider_feature(layer, fid):
    req = QgsFeatureRequest().setFilterFid(int(fid))
    for feat in layer.dataProvider().getFeatures(req):
        return feat
    return None


def _live_names(layer) -> list:
    names = []
    for field in layer.fields():
        if field.name().lower() in _SKIP_FIELDS:
            continue
        names.append(field.name())
    return names


def capture_edits(layer) -> tuple:
    """
    Read the open edit buffer.

    Returns ``(rows, missed)``. Each row keeps the before and after values
    and the geometry of that version. An update keeps the shape after the edit.
    """
    buffer = layer.editBuffer() if layer is not None else None
    if buffer is None or not layer.isModified():
        return [], 0
    deleted = _id_set(buffer.deletedFeatureIds())
    added = _mapping(buffer.addedFeatures())
    changed_attr = _mapping(buffer.changedAttributeValues())
    changed_geom = _id_set(buffer.changedGeometries())
    rows = []
    missed = 0

    def add_row(change_type, before, after, geom, geometry_changed, fid):
        rows.append({
            "change_type": change_type,
            "before": before,
            "after": after,
            "geometry": geom,
            "geometry_changed": geometry_changed,
            "fid": fid,
        })

    for fid in deleted:
        if fid in added:
            continue
        try:
            if int(fid) < 0:
                continue
        except (TypeError, ValueError):
            pass
        feat = _provider_feature(layer, fid)
        if feat is None or not feat.isValid():
            missed += 1
            continue
        before = _values(feat)
        add_row(M.CHANGE_DELETE, before, {}, _clone_geom(feat.geometry()), False, fid)

    update_ids = (set(changed_attr) | changed_geom) - deleted - set(added)
    fields = layer.fields()
    changed_geoms = _mapping(buffer.changedGeometries()) if hasattr(buffer, "changedGeometries") else {}
    for fid in update_ids:
        feat = _provider_feature(layer, fid)
        if feat is None or not feat.isValid():
            missed += 1
            continue
        before = _values(feat)
        after = dict(before)
        for idx, val in _mapping(changed_attr.get(fid)).items():
            try:
                name = fields.field(int(idx)).name()
            except Exception:  # noqa: BLE001
                continue
            if name.lower() in _SKIP_FIELDS:
                continue
            after[name] = py_value(val)
        geom = _clone_geom(feat.geometry())
        if fid in changed_geom:
            updated = _clone_geom(_geom_lookup(changed_geoms, fid))
            if updated is not None:
                geom = updated
        add_row(M.CHANGE_UPDATE, before, after, geom, fid in changed_geom, fid)

    for fid, feat in added.items():
        if fid in deleted:
            continue
        after = _values(feat)
        add_row(M.CHANGE_INSERT, {}, after, _clone_geom(feat.geometry()), False, fid)
    return rows, missed


def _geom_lookup(geoms, fid):
    if fid in geoms:
        return geoms[fid]
    try:
        return geoms.get(int(fid))
    except (TypeError, ValueError):
        return None


def ensure_tracking_fields(layer) -> str:
    """Add version columns the table does not have yet. Returns a warning, or ''."""
    try:
        fields = layer.fields()
        have = {fields.field(i).name().lower() for i in range(fields.count())}
    except Exception as exc:  # noqa: BLE001
        return f"Could not read fields on '{layer.name()}': {exc}"
    missing = [make_field(name, kind, length) for name, kind, length, _desc in ARCHIVE_COLUMNS
               if name.lower() not in have]
    if not missing:
        return ""
    provider = layer.dataProvider()
    if provider is None:
        return f"'{layer.name()}' has no data provider, so the version columns were not added."
    try:
        ok = provider.addAttributes(missing)
    except Exception as exc:  # noqa: BLE001
        return (f"Could not add version columns to '{layer.name()}': {exc} "
                "The change is still recorded with the columns that table already has.")
    if not ok:
        return (f"Could not add version columns to '{layer.name()}'. "
                "The change is still recorded with the columns that table already has.")
    try:
        layer.updateFields()
    except Exception as exc:  # noqa: BLE001
        return f"Version columns were added to '{layer.name()}' but the layer did not refresh: {exc}"
    return ""


def existing_version_max(layer) -> dict:
    """Highest stored ``Version`` for each feature key and source id. Rows with no number do not count."""
    fields = layer.fields()
    v_idx = fields.lookupField("Version")
    if v_idx < 0:
        return {}
    k_idx = fields.lookupField("FeatureKey")
    f_idx = fields.lookupField("SourceFid")
    out = {}
    for feat in layer.getFeatures():
        raw = py_value(feat.attribute(v_idx))
        try:
            ver = int(raw)
        except (TypeError, ValueError):
            continue
        if ver <= 0:
            continue
        tokens = []
        if k_idx >= 0:
            key = py_value(feat.attribute(k_idx))
            if key is not None and str(key).strip():
                tokens.append("k:" + str(key).strip())
        if f_idx >= 0:
            fid = py_value(feat.attribute(f_idx))
            if fid is not None and str(fid).strip():
                tokens.append("f:" + str(fid).strip())
        for token in tokens:
            if ver > out.get(token, 0):
                out[token] = ver
    return out


def _qdatetime(value):
    if value is None:
        return None
    from qgis.PyQt.QtCore import QDateTime, Qt
    if isinstance(value, QDateTime):
        return value
    if isinstance(value, datetime):
        return QDateTime(value.year, value.month, value.day, value.hour, value.minute, value.second)
    text = str(value).strip()
    if not text:
        return None
    fmt = getattr(getattr(Qt, "DateFormat", Qt), "ISODate", None)
    if fmt is None:
        fmt = getattr(Qt, "ISODate", 1)
    parsed = QDateTime.fromString(text, fmt)
    if not parsed.isValid():
        parsed = QDateTime.fromString(text, "yyyy-MM-dd HH:mm:ss")
    return parsed if parsed.isValid() else None


def _coerce(value, field):
    kind = _kind_of(field)
    if kind == "datetime":
        return _qdatetime(value)
    try:
        return convert_value(value, kind)
    except (TypeError, ValueError):
        return None


def write_rows(version_layer, item: dict) -> int:
    """Insert version rows through the provider and reload the layer. Returns the count stored."""
    rows = list(item.get("rows") or [])
    if not rows:
        return 0
    if version_layer is None:
        raise ConfigError("A version table is no longer in the project.")
    if version_layer.isEditable():
        raise ConfigError(f"Stop editing '{version_layer.name()}' so versions can be written to it.")
    warning = ensure_tracking_fields(version_layer)
    if warning:
        item.setdefault("notes", []).append(warning)
    provider = version_layer.dataProvider()
    if provider is None:
        raise ConfigError(f"'{version_layer.name()}' has no data provider.")
    fields = version_layer.fields()
    spatial = _spatial(version_layer)
    prepared_attrs = []
    for raw in rows:
        attrs = M.stored_change(
            raw["change_type"], raw.get("before"), raw.get("after"),
            geometry_changed=bool(raw.get("geometry_changed")),
            feature_key_fields=item.get("key_fields") or (),
            live_names=item.get("live_names"),
            when=item.get("when"), who=item.get("who") or "",
            source_fid=raw.get("fid"))
        if not attrs:
            continue
        prepared_attrs.append((attrs, raw))
    try:
        M.assign_versions([attrs for attrs, _raw in prepared_attrs], existing_version_max(version_layer))
    except Exception as exc:  # noqa: BLE001 - still store the version without a number
        item.setdefault("notes", []).append(f"Version numbers could not be read: {exc}")
    prepared = []
    for attrs, raw in prepared_attrs:
        for key in list(attrs):
            if str(key).startswith("_"):
                attrs.pop(key, None)
        feat = QgsFeature(fields)
        for name, value in attrs.items():
            idx = fields.lookupField(name)
            if idx < 0:
                continue
            feat.setAttribute(idx, _coerce(value, fields.field(idx)))
        geom = raw.get("geometry")
        if spatial and geom is not None:
            g = QgsGeometry(geom)
            _conform(g, version_layer.wkbType())
            feat.setGeometry(g)
        prepared.append(feat)
    if not prepared:
        return 0

    written = 0
    batch = []

    def flush():
        nonlocal written, batch
        if not batch:
            return
        ok, count = _accepted(_add(provider, batch), len(batch))
        if not ok:
            raise ConfigError(
                f"The version table '{version_layer.name()}' saved {written} row(s) and then rejected a batch. "
                f"The live edit is already saved.")
        written += count
        batch = []

    for feat in prepared:
        batch.append(feat)
        if len(batch) >= BATCH:
            flush()
    flush()
    version_layer.updateExtents()
    try:
        version_layer.reload()
    except Exception:  # noqa: BLE001 - the rows are already stored
        pass
    version_layer.triggerRepaint()
    return written


class ArchiveWatcher(QObject):
    """Copy committed edits on the live NENA layers into their version tables."""

    def __init__(self, notify):
        super().__init__()
        self.notify = notify
        self._conns = []
        self._layers = {}
        self._roles = {}
        self._plans = {}
        self._writing = False
        prj = QgsProject.instance()
        prj.readProject.connect(self.refresh)
        prj.layersAdded.connect(self.refresh)
        prj.layersRemoved.connect(self.refresh)
        self.refresh()

    def unload(self):
        prj = QgsProject.instance()
        for sig in (prj.readProject, prj.layersAdded, prj.layersRemoved):
            safe_disconnect(prj, sig, self.refresh)
        self._detach()

    def _detach(self):
        disconnect_all(self._conns)
        self._conns = []
        self._layers = {}
        self._roles = {}
        self._plans = {}

    def _connect(self, owner, sig, slot):
        sig.connect(slot)
        self._conns.append((owner, sig, slot))

    def refresh(self, *_args):
        """Attach to the live layers named in the project, when change tracking is on."""
        self._detach()
        pcfg = load_project_config()
        if not pcfg.archive.enabled:
            return
        live, archive = pairing(pcfg)
        for role in tuple(M.ROLE_ORDER) + ("access",):
            src = live.get(role)
            dest = archive.get(role)
            if src is None or dest is None or src.id() == dest.id():
                continue
            lid = src.id()
            self._layers[lid] = src
            self._roles.setdefault(lid, []).append({
                "role": role,
                "archive_id": dest.id(),
                "key_fields": key_fields_for(src, role, pcfg),
                "live_names": _live_names(src),
            })
        for lid, layer in self._layers.items():
            self._connect(layer, layer.beforeCommitChanges, lambda *_, layer_id=lid: self._before(layer_id))
            self._connect(layer, layer.afterCommitChanges, lambda *_, layer_id=lid: self._after(layer_id))
            rollback = getattr(layer, "afterRollBack", None)
            if rollback is not None:
                self._connect(layer, rollback, lambda *_, layer_id=lid: self._failed(layer_id))
            # QGIS 4 does not expose commitError on the layer. Builds that do clear a failed save.
            commit_error = getattr(layer, "commitError", None)
            if commit_error is not None and hasattr(commit_error, "connect"):
                self._connect(layer, commit_error, lambda *_, layer_id=lid: self._failed(layer_id))
            self._connect(layer, layer.committedFeaturesAdded,
                          lambda _layer_id, feats, layer_id=lid: self._added(layer_id, feats))

    def _before(self, lid):
        if self._writing or lid not in self._roles:
            return
        layer = self._layers.get(lid)
        if layer is None:
            return
        try:
            rows, missed = capture_edits(layer)
            roles = [dict(spec, rows=[dict(row) for row in rows]) for spec in self._roles.get(lid, [])]
            self._plans[lid] = {
                "when": datetime.now().replace(microsecond=0),
                "who": connection_login(layer) or current_user(),
                "missed": missed,
                "roles": roles,
            }
        except Exception as exc:  # noqa: BLE001 - a failed history read must not block the save
            self._plans.pop(lid, None)
            self.notify("Warning", f"Could not read edits for change tracking: {exc}")

    def _added(self, lid, features):
        """Use the committed insert, including the feature id assigned on save."""
        plan = self._plans.get(lid)
        if not plan:
            return
        try:
            feats = list(features or [])
            for spec in plan["roles"]:
                inserts = [row for row in spec["rows"] if row["change_type"] == M.CHANGE_INSERT]
                if len(inserts) != len(feats):
                    continue
                for row, feat in zip(inserts, feats):
                    row["after"] = _values(feat)
                    geom = _clone_geom(feat.geometry())
                    if geom is not None:
                        row["geometry"] = geom
                    row["fid"] = feat.id()
        except Exception:  # noqa: BLE001 - keep the snapshot taken before the save
            return

    def _failed(self, lid):
        self._plans.pop(lid, None)

    def _after(self, lid):
        plan = self._plans.pop(lid, None)
        if not plan:
            return
        if not any(spec["rows"] for spec in plan["roles"]) and not plan.get("missed"):
            return
        QTimer.singleShot(0, lambda item=plan: self._write_plan(item))

    def _write_plan(self, plan):
        self._writing = True
        try:
            total = 0
            notes = []
            when = plan.get("when")
            who = plan.get("who") or ""
            for spec in plan.get("roles") or []:
                if not spec.get("rows"):
                    continue
                lyr = QgsProject.instance().mapLayer(spec["archive_id"])
                payload = {
                    "rows": spec["rows"],
                    "when": when,
                    "who": who,
                    "key_fields": spec.get("key_fields"),
                    "live_names": spec.get("live_names"),
                }
                try:
                    total += write_rows(lyr, payload)
                except ConfigError as exc:
                    notes.append(str(exc))
                notes.extend(payload.get("notes") or [])
            missed = int(plan.get("missed") or 0)
            if missed:
                notes.append(f"{missed} feature(s) could not be read for change tracking.")
            if total:
                self.notify("Info", f"Recorded {total} version(s).")
            if notes:
                self.notify("Warning", " ".join(notes))
        except Exception as exc:  # noqa: BLE001 - the live edit is already saved
            self.notify("Warning", f"Could not write the version: {exc}")
        finally:
            self._writing = False
