# -*- coding: utf-8 -*-
"""Small maintenance tools: NGUID / agency population and a template layer."""

from typing import Set, Tuple

from qgis.core import QgsFeatureRequest, QgsFields, QgsVectorLayer

from ..compat import make_field
from .config import PluginConfig
from .layer_access import ConfigError, FieldAccessor, apply_changes
from .nguid import local_id, make_nguid
from .schema_pack import centerline_defs
from .split_ops import nguid_agency


def populate_ids(layer: QgsVectorLayer, cfg: PluginConfig, selected_only: bool,
                 overwrite: bool = False) -> Tuple[int, int]:
    """
    Fill empty RCL_NGUID values (and empty DiscrpAgID with the agency id).
    A road whose NGUID another road also has (a split piece that kept the
    original's id) gets a new one too; the oldest feature keeps the id.

    The agency is the one in the settings, else the one the layer's
    existing NGUIDs use.
    Returns (nguids_written, agency_written).
    """
    acc = FieldAccessor(layer, cfg)
    if not (acc.has("RCL_NGUID") or acc.has("DiscrpAgID")):
        raise ConfigError("Neither RCL_NGUID nor DiscrpAgID is mapped.")
    agency, fmt = nguid_agency(cfg, layer, acc)
    if not agency:
        raise ConfigError("Enter your agency identifier (e.g. 'psap.example.gov') first: "
                          "the layer has no NGUIDs to take it from.")
    duplicates = _duplicate_nguids(layer, acc) if acc.has("RCL_NGUID") else set()
    feats = layer.getSelectedFeatures() if selected_only else layer.getFeatures()
    changes, n_ids, n_ag = {}, 0, 0
    for f in feats:
        vals = {}
        if acc.has("RCL_NGUID") and (overwrite or not acc.get(f, "RCL_NGUID") or f.id() in duplicates):
            vals["RCL_NGUID"] = make_nguid(local_id(cfg.edit.nguid_local_id, f.id()), agency, fmt)
            n_ids += 1
        if acc.has("DiscrpAgID") and not acc.get(f, "DiscrpAgID"):
            vals["DiscrpAgID"] = agency
            n_ag += 1
        if vals:
            changes[f.id()] = vals
    apply_changes(layer, acc, changes, "NENA: populate NGUID / DiscrpAgID")
    return n_ids, n_ag


def _duplicate_nguids(layer: QgsVectorLayer, acc: FieldAccessor) -> Set:
    """Ids of features whose RCL_NGUID an older feature (lower id) already has."""
    first, later = {}, set()
    request = QgsFeatureRequest().setSubsetOfAttributes([acc.index["RCL_NGUID"]])
    flag = getattr(getattr(QgsFeatureRequest, "Flag", QgsFeatureRequest), "NoGeometry", None)
    if flag is not None and hasattr(request, "setFlags"):
        request.setFlags(flag)
    for f in layer.getFeatures(request):
        value = acc.get(f, "RCL_NGUID")
        if not value:
            continue
        if value in first:
            keep, drop = sorted((first[value], f.id()), key=_fid_key)
            first[value] = keep
            later.add(drop)
        else:
            first[value] = f.id()
    return later


def _fid_key(fid):
    return (0, fid) if isinstance(fid, int) else (1, str(fid))


def create_template_layer(crs_authid: str = "EPSG:4326", name: str = "NENA_RoadCenterlines",
                          template: dict = None) -> QgsVectorLayer:
    """
    In-memory LineString layer with the loaded field template, or the full
    built-in NENA RCL schema (save it to GeoPackage afterwards).
    """
    lyr = QgsVectorLayer(f"LineString?crs={crs_authid}", name, "memory")
    fields = QgsFields()
    for fd in template_field_defs(template):
        fields.append(make_field(fd.physical_name, fd.ftype, fd.length))
    lyr.dataProvider().addAttributes(fields.toList())
    lyr.updateFields()
    return lyr


def default_mapping_for_template(template: dict = None) -> dict:
    return {fd.name: fd.physical_name for fd in template_field_defs(template)}


def template_field_defs(template):
    """Field definitions to create: the template's own fields, or the full built-in schema."""
    return centerline_defs(template)
