# -*- coding: utf-8 -*-
"""Create the address point and driveway layers (GeoPackage or any database connection)."""

from qgis.core import Qgis, QgsFields

from ...compat import make_field
from .. import tables
from ..schema_pack import address_defs, driveway_defs


def address_field_defs(template=None):
    """Built-in NENA SSAP schema, or the state template's own fields + the plugin link fields."""
    return address_defs(template)


def _fields(defs) -> QgsFields:
    out = QgsFields()
    for d in defs:
        out.append(make_field(d.physical_name, d.ftype, d.length))
    return out


def create_layer(kind: str, target: tuple, crs, template=None):
    """
    :param kind: 'address' | 'driveway'
    :param target: ('gpkg', path, table) or ('conn', provider_key, connection, schema, table)
    """
    if kind == "address":
        flds, wkb, label = _fields(address_field_defs(template)), Qgis.WkbType.Point, "Address points"
    else:
        flds, wkb, label = _fields(driveway_defs()), Qgis.WkbType.LineString, "Driveways"
    if target[0] == "gpkg":
        return tables.create_in_geopackage(target[1], target[2], flds, wkb, crs, label)
    return tables.create_in_connection(target[1], target[2], target[3], target[4], flds, wkb, crs, label)
