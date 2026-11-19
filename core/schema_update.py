# -*- coding: utf-8 -*-
"""
Update schema: bring the tables a project already uses up to the plugin's
current field lists, without touching the data in them.

For each table the project uses (roads, street names, address points,
driveways, access points, lookup lists, version tables, check exceptions,
zone layers) this lists the fields the plugin knows that the table lacks.
Tables the plugin expects but the project has none of (access points, lookup
lists, check exceptions) are listed too. Applying adds only the ticked fields
and tables. Nothing is dropped, renamed, retyped, or replaced.

The comparison (``compare_fields``) is plain Python; the rest reads layers.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .schema import MANDATORY, FieldDef, auto_map

# Fields the plugin itself reads or writes. Ticked by default even when optional.
PLUGIN_RCL = {"RCL_NGUID", "StreetID", "FromAddr_L", "ToAddr_L", "FromAddr_R", "ToAddr_R", "Parity_L",
              "Parity_R", "OneWay", "DateUpdate", "WhoEntered", "LastUpdatedBy", "FullName", "LSt_PreDir",
              "LSt_Name", "LSt_Typ", "LSt_PosDir", "LSt_FullName", "PSAP_L", "PSAP_R", "Hierarchy", "TravelTime",
              "Surface", "SpeedLimit", "RoadClass"}
PLUGIN_ADDRESS = {"Site_NGUID", "Add_Number", "StreetID", "RCL_NGUID", "Access_X", "Access_Y", "Addr_Side",
                  "FullName", "AccessPointID", "UnitType", "AddressStatus", "AddressType", "DateUpdate",
                  "WhoEntered", "LastUpdatedBy", "FullAddr"}
SHAPEFILE_NAME_MAX = 10
ARCHIVE_ROLES = ("centerline", "streets", "address", "driveway", "access")


@dataclass
class FieldAdd:
    name: str                     # NENA (logical) name
    physical: str                 # column name to create
    ftype: str = "string"
    length: int = 0
    required: bool = False
    checked: bool = True
    qfield: object = None         # a QgsField to copy (version tables), else built from ftype/length
    note: str = ""


@dataclass
class TableUpdate:
    key: str                      # centerline, streets, address, …, lookup:<key>, version:<role>, zone:<kind>
    title: str
    layer: object = None
    fields: List[FieldAdd] = field(default_factory=list)
    note: str = ""


@dataclass
class MissingTable:
    key: str                      # schema key for Create schema: access, a lookup key, exceptions
    title: str
    can_create: bool = False
    checked: bool = True
    note: str = ""


@dataclass
class UpdatePlan:
    tables: List[TableUpdate] = field(default_factory=list)
    missing: List[MissingTable] = field(default_factory=list)
    container: str = ""           # file the new tables go into, when the roads are in one
    container_kind: str = ""      # gpkg | gdb | ""

    @property
    def empty(self) -> bool:
        return not any(t.fields for t in self.tables) and not self.missing


def compare_fields(defs: Sequence[FieldDef], present: Iterable[str], mapped: Iterable[str] = (),
                   plugin_uses: Optional[set] = None, tick_all: bool = False,
                   shapefile: bool = False) -> List[FieldAdd]:
    """
    Fields in ``defs`` the table does not have.

    A field counts as present when its NENA name is in ``mapped``, its physical
    name or NENA name is a column (any case), or a known alias of it is a
    column. Missing fields are ticked when they are mandatory, used by the
    plugin (``plugin_uses``), or ``tick_all``.
    """
    present = [str(n) for n in present]
    lower = {n.lower() for n in present}
    mapped = set(mapped or ())
    try:
        aliased = set(auto_map(present, list(defs)).keys())
    except Exception:  # noqa: BLE001 - alias matching is a convenience
        aliased = set()
    out = []
    seen = set()
    for d in defs:
        if d.name in seen:
            continue
        seen.add(d.name)
        if d.name in mapped or d.name in aliased:
            continue
        if d.physical_name.lower() in lower or d.name.lower() in lower:
            continue
        add = FieldAdd(d.name, d.physical_name, d.ftype, d.length, d.req == MANDATORY,
                       tick_all or d.req == MANDATORY or (plugin_uses is not None and d.name in plugin_uses))
        if shapefile and len(add.physical) > SHAPEFILE_NAME_MAX:
            add.checked = False
            add.note = "too long for a shapefile column name"
        out.append(add)
    return out


def container_of(layer) -> tuple:
    """(path, 'gpkg' | 'gdb' | '') of the file a layer is stored in."""
    if layer is None:
        return "", ""
    try:
        provider = layer.providerType()
        source = layer.source()
    except Exception:  # noqa: BLE001
        return "", ""
    if provider != "ogr":
        return "", ""
    path = source.split("|")[0]
    low = path.lower().rstrip("/\\")
    if low.endswith(".gpkg"):
        return path, "gpkg"
    if low.endswith(".gdb"):
        return path.rstrip("/\\"), "gdb"
    return "", ""


def _is_shapefile(layer) -> bool:
    try:
        return layer.providerType() == "ogr" and layer.source().split("|")[0].lower().endswith(".shp")
    except Exception:  # noqa: BLE001
        return False


def _names(layer) -> List[str]:
    return [f.name() for f in layer.fields()]


# ---------------------------------------------------------------------------
def plan_update(pcfg, rcl=None, rcl_cfg=None) -> UpdatePlan:
    """What each table the project uses is missing. Nothing is changed."""
    from .addressing.ssap import DRIVEWAY_FIELDS
    from .check_exceptions import FIELDS as EXCEPTION_FIELDS
    from .lookups import LOOKUP_SPECS, lookup_field_defs
    from .project_config import layer_for, resolve_layer
    from .schema_pack import (ARCHIVE_COLUMNS, access_defs, address_defs, centerline_defs, driveway_defs,
                              street_field_defs)
    from . import zones as Z

    plan = UpdatePlan()

    def add(key, title, layer, fields, note=""):
        plan.tables.append(TableUpdate(key, title, layer, fields, note))

    tpl = (rcl_cfg.template if rcl_cfg is not None else None) or None
    if rcl is not None:
        mapped = [k for k, v in (rcl_cfg.mapping if rcl_cfg is not None else {}).items() if v]
        add("centerline", "Road centerlines", rcl,
            compare_fields(centerline_defs(tpl), _names(rcl), mapped, PLUGIN_RCL, shapefile=_is_shapefile(rcl)))
    master = layer_for(pcfg.streets, "master")
    if master is not None:
        add("streets", "Master street names", master,
            compare_fields(street_field_defs(), _names(master), (), None, tick_all=True,
                           shapefile=_is_shapefile(master)))
    addr = layer_for(pcfg.streets, "address")
    if addr is not None:
        mapped = [k for k, v in pcfg.streets.address_mapping.items() if v]
        sid = pcfg.streets.street_id_field
        if sid and addr.fields().lookupField(sid) >= 0:
            mapped.append("StreetID")
        add("address", "Address points", addr,
            compare_fields(address_defs(pcfg.addresses.address_template or None), _names(addr), mapped,
                           PLUGIN_ADDRESS, shapefile=_is_shapefile(addr)))
    dw = layer_for(pcfg.addresses, "driveway")
    if dw is not None:
        mapped = [k for k, v in pcfg.addresses.driveway_mapping.items() if v]
        add("driveway", "Driveways", dw,
            compare_fields(driveway_defs(), _names(dw), mapped, None, tick_all=True, shapefile=_is_shapefile(dw)))
    access = layer_for(pcfg.addresses, "access")
    if access is not None:
        add("access", "Access points", access,
            compare_fields(access_defs(), _names(access), (), None, tick_all=True,
                           shapefile=_is_shapefile(access)))
    else:
        plan.missing.append(MissingTable("access", "Access points (AccessPoint)"))
    for key, table, title, role in LOOKUP_SPECS:
        lyr = layer_for(pcfg.lookups, role)
        if lyr is not None:
            add(f"lookup:{key}", f"Lookup list: {title}", lyr,
                compare_fields(lookup_field_defs(key), _names(lyr), (), None, tick_all=True))
        else:
            plan.missing.append(MissingTable(key, f"Lookup list: {title} ({table})"))
    exc = layer_for(pcfg.zones, "exceptions")
    if exc is not None:
        add("exceptions", "Check exceptions", exc,
            compare_fields(list(EXCEPTION_FIELDS), _names(exc), (), None, tick_all=True))
    else:
        plan.missing.append(MissingTable("exceptions", "Check exceptions (CheckExceptions)"))
    for zone in Z.load_zones(pcfg.zones.layers):
        lyr = resolve_layer(zone.layer_id, zone.layer_source)
        if lyr is None:
            continue
        fields = compare_fields(Z.zone_field_defs(zone.kind), _names(lyr), (), None,
                                shapefile=_is_shapefile(lyr))
        add(f"zone:{zone.kind}:{lyr.id()}", f"Zone layer: {lyr.name()} ({zone.title})", lyr, fields,
            "Zone layers are often kept to a state or regional format, so nothing is ticked here.")

    live = {
        "centerline": rcl, "streets": master, "address": addr, "driveway": dw, "access": access,
    }
    for role in ARCHIVE_ROLES:
        ver = layer_for(pcfg.archive, role)
        src = live.get(role)
        if ver is None:
            continue
        have = {n.lower() for n in _names(ver)}
        fields = []
        if src is not None:
            from .archive_watch import _live_names
            src_fields = src.fields()
            for name in _live_names(src):
                if name.lower() in have:
                    continue
                qf = src_fields.field(src_fields.lookupField(name))
                fields.append(FieldAdd(name, name, "", 0, False, True, qf,
                                       "on the live layer, so versions can store it"))
                have.add(name.lower())
        for name, kind, length, _desc in ARCHIVE_COLUMNS:
            if name.lower() not in have:
                fields.append(FieldAdd(name, name, kind, length, True, True))
        add(f"version:{role}", f"Version table: {ver.name()}", ver, fields)

    plan.container, plan.container_kind = container_of(rcl)
    for item in plan.missing:
        item.can_create = bool(plan.container)
        if not item.can_create:
            item.checked = False
            item.note = ("Your roads are not in a GeoPackage or File Geodatabase. Make this table with "
                         "Create schema (tick only it).")
    return plan


# ---------------------------------------------------------------------------
@dataclass
class UpdateResult:
    added: Dict[str, List[str]] = field(default_factory=dict)   # table title -> columns added
    created: List[str] = field(default_factory=list)            # new tables
    skipped: List[str] = field(default_factory=list)            # notes on what was not done


def apply_fields(plan: UpdatePlan, rcl_cfg=None) -> UpdateResult:
    """Add the ticked fields. A table with unsaved edits is skipped so they are not saved by accident."""
    from ..compat import make_field
    from qgis.core import QgsField
    result = UpdateResult()
    for table in plan.tables:
        wanted = [f for f in table.fields if f.checked]
        if not wanted or table.layer is None:
            continue
        layer = table.layer
        if layer.isEditable() and layer.isModified():
            result.skipped.append(f"{table.title}: save or discard your edits on '{layer.name()}' first.")
            continue
        was_editing = layer.isEditable()
        if not was_editing and not layer.startEditing():
            result.skipped.append(f"{table.title}: '{layer.name()}' cannot be edited.")
            continue
        added = []
        for item in wanted:
            qf = QgsField(item.qfield) if item.qfield is not None else make_field(item.physical, item.ftype or
                                                                                    "string", item.length)
            if layer.addAttribute(qf):
                added.append(item)
            else:
                result.skipped.append(f"{table.title}: could not add {item.physical}.")
        if not layer.commitChanges():
            errors = "; ".join(list(layer.commitErrors())[:3]) if hasattr(layer, "commitErrors") else ""
            layer.rollBack()
            result.skipped.append(f"{table.title}: '{layer.name()}' did not save the new fields. {errors}")
            continue
        if was_editing:
            layer.startEditing()
        layer.updateFields()
        if added:
            result.added[table.title] = [item.physical for item in added]
        if table.key == "centerline" and rcl_cfg is not None:
            names = {f.name().lower(): f.name() for f in layer.fields()}
            for item in added:
                actual = names.get(item.physical.lower())
                if actual:
                    rcl_cfg.mapping[item.name] = actual
    return result


def missing_keys(plan: UpdatePlan) -> List[str]:
    return [m.key for m in plan.missing if m.checked and m.can_create]


def container_target(plan: UpdatePlan):
    """A Create schema target for the file the roads are in."""
    from .schema_writer import SchemaTarget
    if not plan.container:
        return None
    return SchemaTarget(plan.container_kind, path=os.path.normpath(plan.container))
