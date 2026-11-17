# -*- coding: utf-8 -*-
"""
Selection overlay: while address points are selected, show where each was
addressed on the road (a buffered circle on the centerline at its access
point), a dashed line to the rooftop and the number. Canvas items only –
nothing is written. Cleared when the selection is cleared.

Colours: blue = access point on its street's centerline; red = the road has
moved away from the stored access point; hollow grey = no stored access
point (inferred from the driveway, else the nearest point on the street).
"""

from qgis.core import Qgis, QgsFeatureRequest, QgsGeometry, QgsPointXY
from qgis.gui import QgsMapCanvasItem, QgsRubberBand
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont

from ..compat import geometry_type_line
from ..core.addressing import engine as E
from ..core.config import load_from_layer
from ..core.layer_access import ConfigError
from ..core.project_config import layer_for, load_project_config
from ..core.streets import names as N
from ..core.qt_safety import safe_disconnect

BLUE, RED, GREY = QColor(30, 110, 220), QColor(210, 40, 40), QColor(120, 120, 120)


class _Label(QgsMapCanvasItem):
    def __init__(self, canvas, map_pt, text, color):
        super().__init__(canvas)
        self._pt, self._text, self._color = QgsPointXY(map_pt), text, color
        self.setZValue(1000)
        self.updatePosition()

    def paint(self, painter, *args):
        f = QFont()
        f.setPointSize(9)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(255, 255, 255))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(QPointF(8 + dx, -6 + dy), self._text)
        painter.setPen(self._color)
        painter.drawText(QPointF(8, -6), self._text)

    def boundingRect(self):
        return QRectF(0, -22, 12 + 8 * len(self._text), 26)

    def updatePosition(self):
        self.setPos(self.toCanvasCoordinates(self._pt))


class AddressOverlay:

    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.items = []
        self.layer = None
        self.canvas.extentsChanged.connect(self._reposition)
        self.refresh()

    def unload(self):
        self.clear()
        self._disconnect()
        safe_disconnect(self.canvas, self.canvas.extentsChanged, self._reposition)

    def _disconnect(self):
        if self.layer is not None:
            safe_disconnect(self.layer, self.layer.selectionChanged, self._on_selection)
        self.layer = None

    def refresh(self, *args):
        """Re-read the project configuration (address layer, on/off)."""
        self._disconnect()
        self.clear()
        pcfg = load_project_config()
        if not pcfg.addresses.overlay:
            return
        self.layer = layer_for(pcfg.streets, "address")
        if self.layer is not None:
            self.layer.selectionChanged.connect(self._on_selection)
            self._on_selection()

    def clear(self):
        for it in self.items:
            try:
                self.canvas.scene().removeItem(it)
            except (RuntimeError, AttributeError):
                pass
        self.items = []

    def _reposition(self):
        for it in self.items:
            if isinstance(it, _Label):
                it.updatePosition()

    def _context(self):
        pcfg = load_project_config()
        rcl = layer_for(pcfg.streets, "centerline")
        if rcl is None:
            return None
        try:
            return E.AddressContext(pcfg, rcl, load_from_layer(rcl))
        except ConfigError:
            return None

    def _on_selection(self, *args):
        self.clear()
        if self.layer is None:
            return
        ids = self.layer.selectedFeatureIds()
        if not ids:
            return
        ctx = self._context()
        if ctx is None:
            return
        limit = ctx.pcfg.addresses.overlay_limit
        expect = len(ids) <= 20              # also show the number the current settings would give
        upp = self.canvas.mapUnitsPerPixel()
        for n, f in enumerate(ctx.addr.getFeatures(QgsFeatureRequest().setFilterFids(list(ids)))):
            if n >= limit or not f.hasGeometry():
                break
            self._draw(ctx, f, upp, expect)

    def _draw(self, ctx, f, upp, expect):
        roof = ctx.from_addr.transform(E._pt(f.geometry()))
        access = E.stored_access(ctx, f)
        color, hollow = BLUE, False
        sid = N.clean(ctx.addr_acc.get(f, "StreetID")) if ctx.addr_acc.has("StreetID") else ""
        key = N.name_key({p: ctx.addr_acc.get(f, p) for p in N.NAME_PARTS})
        measure = E.StreetMeasure(ctx, sid, key) if (sid or key) else None
        if access is None:
            hollow, color = True, GREY
            if measure is not None and measure.segs:
                loc = measure.locate(roof)
                if loc is not None:
                    g = measure.segs[loc[1]][0]
                    access = QgsPointXY(g.closestSegmentWithContext(roof)[1])
        elif measure is not None and measure.segs:
            loc = measure.locate(access)
            if loc is None or loc[4] ** 0.5 > ctx.tol * 4:
                color = RED
        if access is None:
            return
        rcl = ctx.rcl
        mp_access = self.canvas.mapSettings().layerToMapCoordinates(rcl, access)
        mp_roof = self.canvas.mapSettings().layerToMapCoordinates(rcl, roof)
        circle = QgsGeometry.fromPointXY(mp_access).buffer(6 * upp, 12)
        band = QgsRubberBand(self.canvas, Qgis.GeometryType.Polygon)
        band.setToGeometry(circle, None)
        band.setStrokeColor(color)
        fill = QColor(color)
        fill.setAlpha(0 if hollow else 90)
        band.setFillColor(fill)
        band.setWidth(2)
        self.items.append(band)
        line = QgsRubberBand(self.canvas, geometry_type_line())
        line.setToGeometry(QgsGeometry.fromPolylineXY([mp_access, mp_roof]), None)
        line.setColor(color)
        line.setWidth(2)
        line.setLineStyle(Qt.PenStyle.DashLine)
        self.items.append(line)
        num, suf = E.parse_number(ctx.addr_acc.get(f, "Add_Number"),
                                  ctx.addr_acc.get(f, "AddNum_Suf") if ctx.addr_acc.has("AddNum_Suf") else None)
        text = "" if num is None else f"{num}{suf}"
        if expect and num is not None:
            try:
                plan = E.plan_address(ctx, access, roof, exclude_address=f.id())
                if plan.result.number is not None and plan.result.label != text:
                    text += f"  (range now: {plan.result.label})"
            except ConfigError:
                pass
        try:
            self.items.append(_Label(self.canvas, mp_access, text, color))
        except Exception:  # noqa: BLE001 - labels are a nicety
            pass
