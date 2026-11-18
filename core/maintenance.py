# -*- coding: utf-8 -*-
"""Small maintenance tools: NGUID / agency population and a template layer."""

from typing import Set, Tuple

from qgis.core import QgsFeatureRequest, QgsFields, QgsVectorLayer

from ..compat import make_field
from .config import PluginConfig
from .layer_access import ConfigError, FieldAccessor, apply_changes
from .nguid import local_id, make_nguid
from .schema_pack import centerline_defs
from .split_ops import agency_from_layers, nguid_agency


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


def populate_address_ids(cfg: PluginConfig, centerlines: QgsVectorLayer, selected_only: bool) -> Tuple[int, int]:
    """
    Site_NGUID of the address points and DW_NGUID of the driveways set in
    the project (see populate_point_ids). The agency is the identifier in
    the settings, else the one the existing NGUIDs use.
    Returns (site_nguids_written, driveway_nguids_written).
    """
    from .addressing.engine import address_mapping
    from .addressing.ssap import DRIVEWAY_FIELDS
    from .project_config import layer_for, load_project_config
    from .schema import auto_map
    pcfg = load_project_config()
    addr = layer_for(pcfg.streets, "address")
    dw = layer_for(pcfg.addresses, "driveway")
    addr_acc = dw_acc = None
    if addr is not None:
        acfg = PluginConfig()
        acfg.mapping = address_mapping(addr, pcfg)
        addr_acc = FieldAccessor(addr, acfg)
    if dw is not None:
        dcfg = PluginConfig()
        dcfg.mapping = dict(auto_map([f.name() for f in dw.fields()], DRIVEWAY_FIELDS),
                            **{k: v for k, v in pcfg.addresses.driveway_mapping.items() if v})
        dw_acc = FieldAccessor(dw, dcfg)
    if addr_acc is None and dw_acc is None:
        return 0, 0
    if (cfg.edit.agency_id or "").strip():
        agency, fmt = cfg.edit.agency_id.strip(), cfg.edit.nguid_format
    else:
        rcl_acc = FieldAccessor(centerlines, cfg) if centerlines is not None else None
        agency, fmt = agency_from_layers([(addr, addr_acc, "Site_NGUID"), (dw, dw_acc, "DW_NGUID"),
                                          (centerlines, rcl_acc, "RCL_NGUID"),
                                          (addr, addr_acc, "DiscrpAgID")], cfg.edit.nguid_format)
    if not agency:
        return 0, 0
    # only the selected features of each layer when "selected" was asked
    return (populate_point_ids(addr, addr_acc, "Site_NGUID", "SSAP", cfg, agency, fmt, selected_only),
            populate_point_ids(dw, dw_acc, "DW_NGUID", "DW", cfg, agency, fmt, selected_only))


def populate_point_ids(layer: QgsVectorLayer, acc: FieldAccessor, field: str, kind: str, cfg: PluginConfig,
                       agency: str, fmt: str, selected_only: bool) -> int:
    """
    Fill empty ``field`` NGUIDs (Site_NGUID of address points, DW_NGUID of
    driveways) of ``layer``, and replace ones an older feature already has.
    ``kind`` is the NGUID layer code (SSAP, DW). Returns the number written.
    """
    if layer is None or acc is None or not acc.has(field) or not agency:
        return 0
    duplicates = _duplicate_nguids(layer, acc, field)
    feats = layer.getSelectedFeatures() if selected_only else layer.getFeatures()
    changes = {}
    for f in feats:
        if not acc.get(f, field) or f.id() in duplicates:
            changes[f.id()] = {field: make_nguid(local_id(cfg.edit.nguid_local_id, f.id()), agency,
                                                 fmt or cfg.edit.nguid_format, kind)}
    if changes:
        apply_changes(layer, acc, changes, f"NENA: populate {field}")
    return len(changes)


def _duplicate_nguids(layer: QgsVectorLayer, acc: FieldAccessor, field: str = "RCL_NGUID") -> Set:
    """Ids of features whose ``field`` value an older feature (lower id) already has."""
    first, later = {}, set()
    request = QgsFeatureRequest().setSubsetOfAttributes([acc.index[field]])
    flag = getattr(getattr(QgsFeatureRequest, "Flag", QgsFeatureRequest), "NoGeometry", None)
    if flag is not None and hasattr(request, "setFlags"):
        request.setFlags(flag)
    for f in layer.getFeatures(request):
        value = acc.get(f, field)
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
