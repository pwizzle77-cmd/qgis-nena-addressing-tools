# -*- coding: utf-8 -*-
"""
"Split & Re-range" map tool.

Hover over a centerline of the panel's layer: the split position (respecting
your snapping settings) is previewed and the line highlighted. Click to
split it there – both pieces get re-ranged (proportional, bent through
address points when configured), the new piece gets a fresh NGUID and the
whole operation is ONE undo step. Right-click or Esc leaves the tool.
"""

from qgis.core import QgsFeatureRequest, QgsMessageLog, QgsPointLocator, QgsRectangle
from qgis.gui import QgsMapTool, QgsRubberBand, QgsSnapIndicator, QgsVertexMarker
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from ..compat import geometry_type_line, msg_level
from ..core.layer_access import ConfigError
from ..core.split_ops import split_and_rerange


class SplitRerangeTool(QgsMapTool):

    def __init__(self, canvas, get_target, notify):
        """
        :param get_target: callable -> (layer, cfg) for the panel's centerline layer (or (None, None))
        :param notify: callable(level, text)
        """
        super().__init__(canvas)
        self.get_target = get_target
        self.notify = notify
        self.snap = QgsSnapIndicator(canvas)
        self.marker = QgsVertexMarker(canvas)
        icons = getattr(QgsVertexMarker, "IconType", QgsVertexMarker)
        self.marker.setIconType(icons.ICON_X)
        self.marker.setColor(QColor(220, 30, 30))
        self.marker.setIconSize(16)
        self.marker.setPenWidth(3)
        self.marker.hide()
        self.band = QgsRubberBand(canvas, geometry_type_line())
        self.band.setColor(QColor(255, 140, 0, 180))
        self.band.setWidth(4)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ------------------------------------------------------------------
    def _find(self, event):
        """-> (layer, fid, point on line in layer CRS, geometry) or None"""
        layer, _cfg = self.get_target()
        if layer is None:
            return None
        utils = self.canvas().snappingUtils()
        match = utils.snapToMap(event.mapPoint())
        self.snap.setMatch(match)
        map_pt = match.point() if match.isValid() else event.mapPoint()
        layer_pt = self.toLayerCoordinates(layer, map_pt)
        r = QgsMapTool.searchRadiusMU(self.canvas())
        rect = self.toLayerCoordinates(layer, QgsRectangle(map_pt.x() - r, map_pt.y() - r,
                                                           map_pt.x() + r, map_pt.y() + r))
        best = None
        for f in layer.getFeatures(QgsFeatureRequest().setFilterRect(rect).setNoAttributes()):
            g = f.geometry()
            if g.isNull() or g.isEmpty():
                continue
            sqr, closest, _after, _left = g.closestSegmentWithContext(layer_pt)
            if sqr >= 0 and (best is None or sqr < best[0]):
                best = (sqr, f.id(), closest, g)
        if best is None:
            return None
        return layer, best[1], best[2], best[3]

    def _clear_preview(self):
        self.marker.hide()
        self.band.reset(geometry_type_line())

    # ------------------------------------------------------------------
    def canvasMoveEvent(self, event):  # noqa: N802 - Qt API
        hit = self._find(event)
        if hit is None:
            self._clear_preview()
            return
        layer, _fid, pt, geom = hit
        self.band.setToGeometry(geom, layer)
        self.marker.setCenter(self.toMapCoordinates(layer, pt))
        self.marker.show()

    def canvasReleaseEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.RightButton:
            self.canvas().unsetMapTool(self)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        layer, cfg = self.get_target()
        if layer is None:
            self.notify("Warning", "Choose a centerline layer in a NENA window first.")
            return
        if not layer.isEditable():
            self.notify("Warning", f"Toggle editing on '{layer.name()}' to split features.")
            return
        hit = self._find(event)
        if hit is None:
            self.notify("Info", "No centerline under the cursor.")
            return
        _layer, fid, pt, _geom = hit
        try:
            new_id, text = split_and_rerange(layer, cfg, fid, pt)
        except ConfigError as exc:
            self.notify("Warning", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.notify("Critical", f"Split failed: {exc}")
            return
        self._clear_preview()
        layer.triggerRepaint()
        self.notify("Success", f"Split feature {fid} (new feature {new_id}). {text}")

    def keyPressEvent(self, event):  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.canvas().unsetMapTool(self)
            event.accept()
            return
        event.ignore()

    def deactivate(self):
        self._clear_preview()
        self.snap.setMatch(QgsPointLocator.Match())
        super().deactivate()

    def cleanup(self):
        self._clear_preview()
        for item in (self.marker, self.band):
            try:
                self.canvas().scene().removeItem(item)
            except (RuntimeError, AttributeError) as err:
                QgsMessageLog.logMessage(
                    f"split_tool.cleanup: item was already removed: {err!r}",
                    "NENA Addressing and RCL Tools", msg_level("Info"))
