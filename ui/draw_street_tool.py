# -*- coding: utf-8 -*-
"""
"Draw road" map tool for a master street name.

Pick a street in the Streets list and click "Draw road": digitize the line
like any QGIS line (snapping and Advanced Digitizing work), right-click to
finish. The new centerline gets the street's StreetID and name parts, and is
ranged by distance (one number every N feet along the line, the same spacing
as the house-number settings). If it ends on another road, the usual
"split at new intersection" runs first.
"""

from qgis.core import QgsFeatureRequest, QgsGeometry, QgsVectorLayerUtils, QgsWkbTypes
from qgis.gui import QgsMapToolCapture, QgsMapToolDigitizeFeature
from qgis.PyQt.QtCore import QTimer

from ..core import geometry_utils as gu
from ..core.layer_access import ConfigError
from ..core.project_config import load_project_config
from ..core.qt_safety import alive
from ..core.ranger import RangeEngine, distance_overrides


def _line_mode():
    modes = getattr(QgsMapToolCapture, "CaptureMode", QgsMapToolCapture)
    return modes.CaptureLine


class DrawStreetTool(QgsMapToolDigitizeFeature):
    """Digitize one road for a master street, then name and range it."""

    def __init__(self, dock, layer, sid, street_name):
        canvas = dock.iface.mapCanvas()
        super().__init__(canvas, dock.iface.cadDockWidget(), _line_mode())
        self.dock = dock
        self.target = layer
        self.sid = sid
        self.street_name = street_name
        self.setLayer(layer)
        self.digitizingCompleted.connect(self._completed)

    # ------------------------------------------------------------------
    def _completed(self, feature):
        layer = self.target
        if not alive(layer):
            return
        geom = QgsGeometry(feature.geometry())
        canvas = self.canvas()
        canvas.unsetMapTool(self)
        if geom.isNull() or geom.isEmpty() or geom.length() <= 0:
            self.dock._msg("Nothing was drawn.", "Warning")
            return
        if QgsWkbTypes.isMultiType(layer.wkbType()) and not geom.isMultipart():
            geom.convertToMultiType()
        if not layer.isEditable():
            layer.startEditing()
        new = QgsVectorLayerUtils.createFeature(layer, geom, {}, layer.createExpressionContext())
        # A normal (non-"NENA:") command, so the edit watcher splits roads it ends on
        # and gives it an NGUID, exactly as when you draw with the QGIS tool.
        layer.beginEditCommand(f"Draw {self.street_name}")
        ok = layer.addFeature(new)
        layer.endEditCommand()
        if not ok:
            self.dock._msg("QGIS did not accept the new road.", "Warning")
            return
        # let the edit watcher finish its own work first
        QTimer.singleShot(50, lambda: self.dock._guard(lambda: self._name_and_range(layer, geom)))

    def _pieces(self, layer, geom):
        """The new road, or its pieces if it was split where it crosses another road."""
        tol = gu.metres_to_layer_units(0.05, layer.crs())
        zone = geom.buffer(tol, 4)
        rect = geom.boundingBox()
        rect.grow(tol)
        out = []
        for f in layer.getFeatures(QgsFeatureRequest().setFilterRect(rect).setNoAttributes()):
            g = f.geometry()
            if g is not None and not g.isEmpty() and zone.contains(g):
                out.append(f.id())
        return out

    def _name_and_range(self, layer, geom):
        if not alive(layer):
            return
        fids = self._pieces(layer, geom)
        if not fids:
            raise ConfigError("The new road was drawn, but it could not be found to name and range it.")
        svc = self.dock.streets.service()
        svc.assign(self.sid, fids)
        cfg = self.dock.collect()
        overrides = distance_overrides(load_project_config(), cfg.ranging)
        layer.selectByIds(fids)
        report = RangeEngine(layer, cfg).run(True, overrides, command="NENA: range new road")
        layer.triggerRepaint()
        self.dock.streets.refresh_list()
        units = {"feet": "ft", "us_survey_feet": "US ft", "meters": "m"}.get(
            overrides["interval_units"], overrides["interval_units"])
        self.dock._msg(f"Drew {self.street_name} ({len(fids)} segment(s)): named and ranged at one number "
                       f"every {overrides['interval_distance']:g} {units}. {report.updated} updated. "
                       "Save the layer to keep it.", "Success")
