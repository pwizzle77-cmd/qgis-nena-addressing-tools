# -*- coding: utf-8 -*-
"""
QGIS project relations + drop-down widgets for StreetID.

After this runs, the StreetID field on centerlines and address points is a
searchable drop-down of active master names in attribute forms and the
attribute table, so nobody types a street name by hand.
"""

from qgis.core import QgsEditorWidgetSetup, QgsProject, QgsRelation

from .repository import F_FULL, F_ID, F_STATUS


def setup_relations(master, linked) -> int:
    """
    :param linked: [(layer, street_id_field_name)]
    :returns: number of relations created
    """
    mgr = QgsProject.instance().relationManager()
    n = 0
    for layer, fname in linked:
        if layer is None or not fname or layer.fields().lookupField(fname) < 0:
            continue
        rid = f"nena_street_{layer.id()}"
        if mgr.relation(rid).isValid():
            mgr.removeRelation(rid)
        rel = QgsRelation()
        rel.setId(rid)
        rel.setName(f"{layer.name()} → {master.name()}")
        rel.setReferencingLayer(layer.id())
        rel.setReferencedLayer(master.id())
        rel.addFieldPair(fname, F_ID)
        if rel.isValid():
            mgr.addRelation(rel)
            n += 1
        idx = layer.fields().lookupField(fname)
        layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("ValueRelation", {
            "Layer": master.id(), "LayerName": master.name(), "LayerSource": master.source(),
            "LayerProviderName": master.providerType(),
            "Key": F_ID, "Value": F_FULL, "OrderByValue": True, "AllowNull": True, "AllowMulti": False,
            "UseCompleter": True, "NofColumns": 1,
            "FilterExpression": f"\"{F_STATUS}\" IS NULL OR \"{F_STATUS}\" <> 'Retired'"}))
    QgsProject.instance().setDirty(True)
    return n
