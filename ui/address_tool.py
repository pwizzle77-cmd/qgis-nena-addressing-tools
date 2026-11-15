# -*- coding: utf-8 -*-
"""
Address & driveway map tool.

1. Click the centerline at the driveway entrance (or an existing driveway to
   share it). The number is calculated from this access point.
2. Optional clicks follow the driveway's bends.
3. Right-click or double-click on the rooftop finishes.

Backspace removes the last click, Esc starts over (a second Esc leaves the
tool). The calculated address is shown for confirmation before anything is
written.
"""

from qgis.core import QgsGeometry, QgsPointLocator, QgsPointXY
from qgis.gui import QgsMapTool, QgsRubberBand, QgsSnapIndicator, QgsVertexMarker
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from ..compat import geometry_type_line
from ..core.addressing import engine as E
from ..core.layer_access import ConfigError
from .address_dialogs import AddressDialog


class AddressTool(QgsMapTool):

    def __init__(self, canvas, get_context, notify, on_created=None):
        """
        :param get_context: callable -> AddressContext (raises ConfigError when not configured)
        :param on_created: callable(ids dict) after an address was written
        """
        super().__init__(canvas)
        self.get_context = get_context
        self.notify = notify
        self.on_created = on_created
        self.ctx = None
        self.points = []            # centerline-layer CRS: [access, bend, …]
        self.segment_fid = None
        self.parent_dw = ""
        self._skip_release = False
        self.snap = QgsSnapIndicator(canvas)
        self.marker = QgsVertexMarker(canvas)
        icons = getattr(QgsVertexMarker, "IconType", QgsVertexMarker)
        self.marker.setIconType(icons.ICON_CIRCLE)
        self.marker.setColor(QColor(30, 110, 220))
        self.marker.setIconSize(14)
        self.marker.setPenWidth(3)
        self.marker.hide()
        self.band = QgsRubberBand(canvas, geometry_type_line())
        self.band.setColor(QColor(30, 110, 220, 200))
        self.band.setWidth(2)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ------------------------------------------------------------------ helpers
    def activate(self):
        super().activate()
        self._reset()
        try:
            self.ctx = self.get_context()
        except ConfigError as exc:
            self.ctx = None
            self.notify("Warning", str(exc))

    def _reset(self):
        self.points, self.segment_fid, self.parent_dw = [], None, ""
        self.band.reset(geometry_type_line())
        self.marker.hide()

    def _layer_pt(self, event):
        match = self.canvas().snappingUtils().snapToMap(event.mapPoint())
        self.snap.setMatch(match)
        mp = match.point() if match.isValid() else event.mapPoint()
        return self.toLayerCoordinates(self.ctx.rcl, mp)

    def _map_pt(self, pt):
        return self.toMapCoordinates(self.ctx.rcl, QgsPointXY(pt))

    def _radius(self):
        r = QgsMapTool.searchRadiusMU(self.canvas())
        a = self.toLayerCoordinates(self.ctx.rcl, QgsPointXY(0, 0))
        b = self.toLayerCoordinates(self.ctx.rcl, QgsPointXY(r, 0))
        return max(abs(b.x() - a.x()), abs(b.y() - a.y()), 1e-9)

    def _access_candidate(self, pt):
        """(segment fid, access point, parent driveway id) for a first click, or None."""
        hit = E.shared_driveway_at(self.ctx, pt, self._radius())
        if hit is not None:
            dw, access = hit
            snapped = E.snap_access(self.ctx, access)
            if snapped is None:
                return None
            dw_id = self.ctx.dw_acc.get(dw, "DW_NGUID") if self.ctx.dw_acc.has("DW_NGUID") else ""
            return snapped[0], snapped[1], str(dw_id or dw.id())
        snapped = E.snap_access(self.ctx, pt)
        if snapped is None:
            return None
        g = QgsGeometry.fromPointXY(pt).distance(QgsGeometry.fromPointXY(snapped[1]))
        if g > self._radius() * 3:
            return None
        return snapped[0], snapped[1], ""

    # ------------------------------------------------------------------ events
    def canvasMoveEvent(self, event):  # noqa: N802
        if self.ctx is None:
            return
        pt = self._layer_pt(event)
        if not self.points:
            cand = self._access_candidate(pt)
            if cand is None:
                self.marker.hide()
                return
            self.marker.setCenter(self._map_pt(cand[1]))
            self.marker.show()
            return
        chain = self.points + [pt]
        self.band.setToGeometry(QgsGeometry.fromPolylineXY(chain), self.ctx.rcl)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if self._skip_release:              # the release that ends a double-click
            self._skip_release = False
            return
        if self.ctx is None:
            try:
                self.ctx = self.get_context()
            except ConfigError as exc:
                self.notify("Warning", str(exc))
                return
        pt = self._layer_pt(event)
        if event.button() == Qt.MouseButton.RightButton:
            if not self.points:
                self.canvas().unsetMapTool(self)
                return
            self._finish(pt)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.points:
            cand = self._access_candidate(pt)
            if cand is None:
                self.notify("Info", "Click on a centerline (or a driveway) where the driveway meets the road.")
                return
            self.segment_fid, access, self.parent_dw = cand
            self.points = [access] + ([pt] if self.parent_dw else [])
            self.marker.setCenter(self._map_pt(access))
            self.marker.show()
            return
        self.points.append(pt)

    def canvasDoubleClickEvent(self, event):  # noqa: N802
        if self.ctx is None or len(self.points) < 2:
            return
        rooftop = self.points.pop()          # the release before the double-click added it
        self._skip_release = True
        self._finish(rooftop)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if self.points:
                self._reset()
            else:
                self.canvas().unsetMapTool(self)
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and self.points:
            self.points.pop()
            if not self.points:
                self._reset()
            event.accept()
            return
        event.ignore()

    def deactivate(self):
        self._reset()
        self.snap.setMatch(QgsPointLocator.Match())
        super().deactivate()

    # ------------------------------------------------------------------ finish
    def _finish(self, rooftop):
        access, vertices = self.points[0], self.points[1:]
        try:
            plan = E.plan_address(self.ctx, access, rooftop, vertices, self.segment_fid,
                                  parent_driveway=self.parent_dw)
        except ConfigError as exc:
            self.notify("Warning", str(exc))
            self._reset()
            return
        a = self.ctx.pcfg.addresses
        dlg = AddressDialog(self.canvas().window(), plan, a.placement, self.ctx.dw is not None, a.create_driveway)
        if dlg.exec():
            v = dlg.values()
            try:
                ids = E.commit_address(self.ctx, plan, v["number"], v["suffix"], v["unit"], v["placement"],
                                       v["create_driveway"])
            except ConfigError as exc:
                self.notify("Warning", str(exc))
            else:
                self.notify("Success", f"Address {v['number']}{v['suffix']} {plan.full_name} created"
                                       + (" with driveway." if "driveway" in ids else "."))
                self.ctx.addr.triggerRepaint()
                if self.on_created:
                    self.on_created(ids)
        self._reset()

    def cleanup(self):
        self._reset()
        for item in (self.marker, self.band):
            try:
                self.canvas().scene().removeItem(item)
            except (RuntimeError, AttributeError):
                pass
