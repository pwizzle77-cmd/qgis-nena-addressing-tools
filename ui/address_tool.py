# -*- coding: utf-8 -*-
"""
Map tools for addresses and driveways.

Place address: click the centerline (or an existing driveway) for the access
point, then click the rooftop. A driveway is not drawn unless Shift is held
or the dialog checkbox is on.

Draw driveway: click the access point, optional bends, then right-click or
double-click to finish. The line can later be shared by address points.

Place units: one access click, then a rooftop click per unit. Right-click or
Esc ends that access. A small panel chooses the numbering.
"""

from qgis.core import QgsGeometry, QgsPointLocator, QgsPointXY
from qgis.gui import QgsMapTool, QgsRubberBand, QgsSnapIndicator, QgsVertexMarker
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QLabel, QLineEdit, QSpinBox,
                                 QVBoxLayout)

from ..compat import geometry_type_line
from ..core.addressing import engine as E
from ..core.addressing.units import next_unit, preview
from ..core.layer_access import ConfigError
from .address_dialogs import AddressDialog


def _band_vertices(band):
    icons = getattr(QgsRubberBand, "IconType", None)
    if icons is None:
        return
    band.setIcon(icons.ICON_CIRCLE)
    band.setIconSize(8)


class _AccessTool(QgsMapTool):
    """Snapping and the first click, shared by the three addressing tools."""

    def __init__(self, canvas, get_context, notify, color):
        super().__init__(canvas)
        self.get_context = get_context
        self.notify = notify
        self.ctx = None
        self.points = []
        self.segment_fid = None
        self.parent_dw = ""
        self._skip_release = False
        self.snap = QgsSnapIndicator(canvas)
        self.marker = QgsVertexMarker(canvas)
        icons = getattr(QgsVertexMarker, "IconType", QgsVertexMarker)
        self.marker.setIconType(icons.ICON_CIRCLE)
        self.marker.setColor(color)
        self.marker.setIconSize(14)
        self.marker.setPenWidth(3)
        self.marker.hide()
        self.band = QgsRubberBand(canvas, geometry_type_line())
        self.band.setColor(QColor(color.red(), color.green(), color.blue(), 200))
        self.band.setWidth(2)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def activate(self):
        super().activate()
        self._reset()
        self._prepare()

    def _prepare(self):
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
        gap = QgsGeometry.fromPointXY(pt).distance(QgsGeometry.fromPointXY(snapped[1]))
        if gap > self._radius() * 3:
            return None
        return snapped[0], snapped[1], ""

    def _ensure_context(self):
        if self.ctx is not None:
            return True
        try:
            self.ctx = self.get_context()
        except ConfigError as exc:
            self.notify("Warning", str(exc))
            return False
        return True

    def _take_access(self, pt, spur=False):
        cand = self._access_candidate(pt)
        if cand is None:
            self.notify("Info", "Click on a centerline, or on a driveway, where access meets the road.")
            return False
        self.segment_fid, access, self.parent_dw = cand
        # An address records the road access. A driveway spur starts on the driveway that was clicked.
        self.points = [pt if (spur and self.parent_dw) else access]
        self.marker.setCenter(self._map_pt(self.points[0]))
        self.marker.show()
        return True

    def _hover_access(self, pt):
        cand = self._access_candidate(pt)
        if cand is None:
            self.marker.hide()
            return
        self.marker.setCenter(self._map_pt(cand[1]))
        self.marker.show()

    def _draw(self, extra=None):
        chain = list(self.points)
        if extra is not None:
            chain.append(extra)
        if len(chain) >= 2:
            self.band.setToGeometry(QgsGeometry.fromPolylineXY(chain), self.ctx.rcl)
        else:
            self.band.reset(geometry_type_line())
        if chain:
            self.marker.setCenter(self._map_pt(chain[0]))
            self.marker.show()

    def deactivate(self):
        self._reset()
        self.snap.setMatch(QgsPointLocator.Match())
        super().deactivate()

    def cleanup(self):
        self._reset()
        for item in (self.marker, self.band):
            try:
                self.canvas().scene().removeItem(item)
            except (RuntimeError, AttributeError):
                pass


class AddressTool(_AccessTool):
    """Click the access point, then the rooftop. Shift draws a straight driveway."""

    def __init__(self, canvas, get_context, notify, on_created=None):
        super().__init__(canvas, get_context, notify, QColor(30, 110, 220))
        self.on_created = on_created

    def canvasMoveEvent(self, event):  # noqa: N802
        if self.ctx is None:
            return
        pt = self._layer_pt(event)
        if not self.points:
            self._hover_access(pt)
            return
        self._draw(pt)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if self._skip_release:
            self._skip_release = False
            return
        if not self._ensure_context():
            return
        pt = self._layer_pt(event)
        if event.button() == Qt.MouseButton.RightButton:
            if self.points:
                self._reset()
            else:
                self.canvas().unsetMapTool(self)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.points:
            self._take_access(pt)
            return
        straight = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._finish(pt, straight)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if self.points:
                self._reset()
            else:
                self.canvas().unsetMapTool(self)
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and self.points:
            self._reset()
            event.accept()
            return
        event.ignore()

    def _finish(self, rooftop, straight):
        access = self.points[0]
        try:
            plan = E.plan_address(self.ctx, access, rooftop, [], self.segment_fid,
                                  parent_driveway=self.parent_dw)
        except ConfigError as exc:
            self.notify("Warning", str(exc))
            self._reset()
            return
        a = self.ctx.pcfg.addresses
        want = True if straight else a.create_driveway
        dlg = AddressDialog(self.canvas().window(), plan, a.placement, self.ctx.dw is not None, want)
        if dlg.exec():
            v = dlg.values()
            try:
                ids = E.commit_address(self.ctx, plan, v["number"], v["suffix"], v["unit"], v["placement"],
                                       v["create_driveway"])
            except ConfigError as exc:
                self.notify("Warning", str(exc))
            else:
                tail = " with a straight driveway." if "driveway" in ids else "."
                self.notify("Success", f"Address {v['number']}{v['suffix']} {plan.full_name} created{tail}")
                self.ctx.addr.triggerRepaint()
                if self.on_created:
                    self.on_created(ids)
        self._reset()


class DrawDrivewayTool(_AccessTool):
    """Digitize a driveway path. It is not tied to an address until one snaps to it."""

    def __init__(self, canvas, get_context, notify, on_created=None):
        super().__init__(canvas, get_context, notify, QColor(180, 90, 20))
        _band_vertices(self.band)
        self.on_created = on_created

    def canvasMoveEvent(self, event):  # noqa: N802
        if self.ctx is None:
            return
        pt = self._layer_pt(event)
        if not self.points:
            self._hover_access(pt)
            return
        self._draw(pt)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if self._skip_release:
            self._skip_release = False
            return
        if not self._ensure_context():
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
            self._take_access(pt, spur=True)
            return
        self.points.append(pt)
        self._draw()

    def canvasDoubleClickEvent(self, event):  # noqa: N802
        if self.ctx is None or len(self.points) < 2:
            return
        end = self.points.pop()
        self._skip_release = True
        self._finish(end)

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
            else:
                self._draw()
            event.accept()
            return
        event.ignore()

    def _finish(self, end):
        if self.ctx.dw is None:
            self.notify("Warning", "Choose a driveway layer on the Addresses tab first.")
            self._reset()
            return
        try:
            fid = E.commit_driveway(self.ctx, self.points[0], list(self.points[1:]) + [end],
                                    self.segment_fid, self.parent_dw)
        except ConfigError as exc:
            self.notify("Warning", str(exc))
            self._reset()
            return
        self.notify("Success", "Driveway created. Place an address on it to link them.")
        self.ctx.dw.triggerRepaint()
        if self.on_created:
            self.on_created(fid)
        self._reset()


class UnitsPanel(QFrame):
    """Base number, numbering scheme, and the next unit. Floats over the map."""

    sessionEnd = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setWindowTitle("Place units")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(True)
        self.setFrameShape(QFrame.Shape.Panel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.index = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        form = QFormLayout()
        self.sp_base = QSpinBox()
        self.sp_base.setRange(0, 9999999)
        self.sp_base.setSpecialValueText("from first rooftop")
        self.sp_base.setToolTip("Street number shared by these units. 0 uses the number calculated for the first rooftop.")
        self.sp_base.valueChanged.connect(self._refresh)
        form.addRow("Base number", self.sp_base)
        self.cb_scheme = QComboBox()
        self.cb_scheme.addItem("101, 102, 103", "increment")
        self.cb_scheme.addItem("101A, 101B, 101C", "letter")
        self.cb_scheme.addItem("1, 2, 3  or  Unit 1", "unit")
        self.cb_scheme.currentIndexChanged.connect(self._scheme)
        form.addRow("Numbering", self.cb_scheme)
        self.ed_prefix = QLineEdit()
        self.ed_prefix.setPlaceholderText("Unit ")
        self.ed_prefix.setToolTip("Text before the unit number. Leave it empty for 1, 2, 3. Type Unit and a space for Unit 1.")
        self.ed_prefix.textChanged.connect(self._refresh)
        self.lbl_prefix = QLabel("Unit prefix")
        form.addRow(self.lbl_prefix, self.ed_prefix)
        self.sp_start = QSpinBox()
        self.sp_start.setRange(1, 9999)
        self.sp_start.setValue(1)
        self.sp_start.setToolTip("First unit number, or the first letter (1 = A).")
        self.sp_start.valueChanged.connect(self._refresh)
        form.addRow("Start at", self.sp_start)
        lay.addLayout(form)
        self.lbl_next = QLabel()
        self.lbl_next.setWordWrap(True)
        lay.addWidget(self.lbl_next)
        self.chk_dw = QCheckBox("Straight driveway for each unit")
        self.chk_dw.setToolTip("Off by default. Most multi-unit sites share the access point and do not need a new line.")
        lay.addWidget(self.chk_dw)
        self._scheme()

    def begin(self, canvas, create_driveway, driveway_available):
        self.index = 0
        self.sp_base.setValue(0)
        self.chk_dw.setChecked(bool(create_driveway) and driveway_available)
        self.chk_dw.setEnabled(driveway_available)
        self._refresh()
        self.adjustSize()
        origin = canvas.mapToGlobal(canvas.rect().topLeft())
        self.move(origin.x() + 16, origin.y() + 16)
        self.show()
        self.raise_()

    def end(self):
        self.hide()

    def note_placed(self):
        self.index += 1
        self._refresh()

    def values(self):
        return dict(scheme=self.cb_scheme.currentData(), base=self.sp_base.value(),
                    prefix=self.ed_prefix.text(), start=self.sp_start.value(),
                    index=self.index, create_driveway=self.chk_dw.isChecked())

    def set_base(self, number):
        self.sp_base.setValue(int(number))

    def _scheme(self, *_args):
        unit = self.cb_scheme.currentData() == "unit"
        self.lbl_prefix.setVisible(unit)
        self.ed_prefix.setVisible(unit)
        self._refresh()

    def _refresh(self, *_args):
        v = self.values()
        if v["base"] <= 0:
            shown = "(first rooftop)"
        else:
            number, suffix, unit = next_unit(v["scheme"], v["index"], v["base"], v["prefix"], v["start"])
            shown = preview(v["scheme"], number, suffix, unit)
        self.lbl_next.setText(f"Next: {shown}    Placed: {self.index}")

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.sessionEnd.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class PlaceUnitsTool(_AccessTool):
    """One shared access point, then a rooftop click for each unit."""

    def __init__(self, canvas, get_context, notify, on_created=None):
        super().__init__(canvas, get_context, notify, QColor(46, 125, 50))
        self.on_created = on_created
        self.panel = UnitsPanel(canvas.window())
        self.panel.sessionEnd.connect(self._end_session)

    def _reset(self):
        super()._reset()
        if getattr(self, "panel", None) is not None:
            self.panel.end()

    def canvasMoveEvent(self, event):  # noqa: N802
        if self.ctx is None:
            return
        pt = self._layer_pt(event)
        if not self.points:
            self._hover_access(pt)
            return
        self._draw(pt)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if not self._ensure_context():
            return
        pt = self._layer_pt(event)
        if event.button() == Qt.MouseButton.RightButton:
            if self.points:
                self._end_session()
            else:
                self.canvas().unsetMapTool(self)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.points:
            if self._take_access(pt):
                a = self.ctx.pcfg.addresses
                self.panel.begin(self.canvas(), a.create_driveway, self.ctx.dw is not None)
            return
        self._place(pt)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._end_session()
            event.accept()
            return
        event.ignore()

    def _end_session(self):
        if not self.points and not self.panel.isVisible():
            self.canvas().unsetMapTool(self)
            return
        self._reset()

    def _place(self, rooftop):
        access = self.points[0]
        try:
            plan = E.plan_address(self.ctx, access, rooftop, [], self.segment_fid,
                                  parent_driveway=self.parent_dw)
        except ConfigError as exc:
            self.notify("Warning", str(exc))
            return
        v = self.panel.values()
        base = v["base"]
        if base <= 0:
            if plan.result.number is None or plan.result.needs_choice:
                self.notify("Warning", "This rooftop needs a number choice. Set the base number in the "
                                       "Place units panel, or use Place address for this one.")
                return
            base = plan.result.number
            self.panel.set_base(base)
            v = self.panel.values()
        number, suffix, unit = next_unit(v["scheme"], v["index"], base, v["prefix"], v["start"])
        try:
            ids = E.commit_address(self.ctx, plan, number, suffix, unit, self.ctx.pcfg.addresses.placement,
                                   v["create_driveway"])
        except ConfigError as exc:
            self.notify("Warning", str(exc))
            return
        self.panel.note_placed()
        label = preview(v["scheme"], number, suffix, unit)
        self.notify("Success", f"Address {label} {plan.full_name} created.")
        self.ctx.addr.triggerRepaint()
        if self.on_created:
            self.on_created(ids)

    def cleanup(self):
        panel = getattr(self, "panel", None)
        super().cleanup()
        if panel is not None:
            panel.hide()
            panel.deleteLater()
            self.panel = None
