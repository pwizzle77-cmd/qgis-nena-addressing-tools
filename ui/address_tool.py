# -*- coding: utf-8 -*-
"""
Map tools for addresses and driveways.

Place address: click the centerline (or an existing driveway) for the access
point, then click the rooftop. The access point is stored 10 feet straight
off the road click. A driveway is not drawn.

Draw driveway: click the access point, optional bends, then right-click or
double-click to finish. The line can later be shared by address points.

Place units: one click on the road takes the house number. Each later click
is another unit under that roof. The house number stays. The unit number
changes. Right-click or Esc ends that roof.
"""

from qgis.core import QgsGeometry, QgsPointLocator, QgsPointXY
from qgis.gui import QgsMapTool, QgsRubberBand, QgsSnapIndicator, QgsVertexMarker
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit,
                                 QSpinBox, QVBoxLayout)

from .widgets import Surface, apply_panel_fill

from ..compat import geometry_type_line
from ..core.addressing import engine as E
from ..core.addressing.units import next_unit
from ..core.layer_access import ConfigError
from ..core import lookups as LK
from ..core.lookups import unit_type_choices
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

    def _layer_pt(self, event, building=False):
        """The click in centerline coordinates, snapped by the QGIS snapping settings.

        A building (rooftop or unit) click never snaps onto a road: a point pulled
        onto the centerline has no side, and one pulled onto a cross street at an
        intersection would be measured from the wrong road.
        """
        match = self.canvas().snappingUtils().snapToMap(event.mapPoint())
        if building and match.isValid() and self.ctx is not None and match.layer() is self.ctx.rcl:
            match = QgsPointLocator.Match()
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
    """Click the road, then the rooftop. The access point is saved 10 feet off the road."""

    def __init__(self, canvas, get_context, notify, on_created=None):
        super().__init__(canvas, get_context, notify, QColor(30, 110, 220))
        self.on_created = on_created

    def canvasMoveEvent(self, event):  # noqa: N802
        if self.ctx is None:
            return
        pt = self._layer_pt(event, building=bool(self.points))
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
        pt = self._layer_pt(event, building=bool(self.points))
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
        self._finish(pt)

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

    def _finish(self, rooftop):
        access = self.points[0]
        try:
            plan = E.plan_address(self.ctx, access, rooftop, [], self.segment_fid,
                                  parent_driveway=self.parent_dw)
        except ConfigError as exc:
            self.notify("Warning", str(exc))
            self._reset()
            return
        pcfg = self.ctx.pcfg
        dlg = AddressDialog(self.canvas().window(), plan, LK.default_placement(pcfg), False, False,
                            unit_type_choices(pcfg), statuses=LK.choices_for(pcfg, "address_status"),
                            types=LK.choices_for(pcfg, "address_type"), placements=LK.choices_for(pcfg, "Placement"),
                            defaults=LK.field_defaults(pcfg, "address"))
        if dlg.exec():
            v = dlg.values()
            try:
                ids = E.commit_address(self.ctx, plan, v["number"], v["suffix"], v["unit"], v["placement"],
                                       False, unit_type=v.get("unit_type") or "",
                                       address_status=v.get("address_status") or "",
                                       address_type=v.get("address_type") or "")
            except ConfigError as exc:
                self.notify("Warning", str(exc))
            else:
                self.notify("Success", f"Address {v['number']}{v['suffix']} {plan.full_name} created."
                            + self._access_note(ids))
                self._nguid_note(ids)
                self.ctx.addr.triggerRepaint()
                if self.ctx.access is not None:
                    self.ctx.access.triggerRepaint()
                if self.on_created:
                    self.on_created(ids)
        self._reset()

    def _nguid_note(self, ids):
        """Say (once per tool) why a new address point has no Site_NGUID."""
        if ids.get("no_site_nguid") and not getattr(self, "_told_no_nguid", False):
            self._told_no_nguid = True
            self.notify("Warning", "The new address point has no Site_NGUID: enter your agency identifier "
                                   "(e.g. columbiacountyfla.com) in Settings › Editing › NGUID, then "
                                   "'Fill empty – all' there fills the ones left empty.")

    def _access_note(self, ids):
        if ids.get("access_created"):
            return " An access point was saved 10 feet off the road, linked to this address and the centerline."
        if ids.get("access"):
            return " It uses the access point already there."
        if self.ctx.access is None:
            return " Choose an access-point layer in the Addresses window to store the access point."
        return ""


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
            self.notify("Warning", "Choose a driveway layer in the Addresses window first.")
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


class UnitsPanel(Surface):
    """House number, unit type, and the next unit. A normal window, not a dock."""

    sessionEnd = pyqtSignal()

    def __init__(self, parent=None):
        flags = Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint
        super().__init__(parent, flags)
        self.setObjectName("NenaPlaceUnitsWindow")
        self.setWindowTitle("Place units")
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.index = 0
        self.house_touched = False
        self._setting_base = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        form = QFormLayout()
        self.cb_type = QComboBox()
        self.cb_type.setToolTip("Unit type from the Unit Type list. The address stores the code.")
        form.addRow("Unit type", self.cb_type)
        self.sp_base = QSpinBox()
        self.sp_base.setRange(0, 9999999)
        self.sp_base.setSpecialValueText("click the road")
        self.sp_base.setToolTip("House number from the road click. Every unit under this roof keeps it. "
                                "Change it here if this roof needs a different number.")
        self.sp_base.valueChanged.connect(self._base_edited)
        form.addRow("House number", self.sp_base)
        self.cb_scheme = QComboBox()
        self.cb_scheme.addItem("1, 2, 3", "unit")
        self.cb_scheme.addItem("101, 102, 103", "unit101")
        self.cb_scheme.addItem("A, B, C", "alpha")
        self.cb_scheme.addItem("1A, 1B, 1C", "pair")
        self.cb_scheme.addItem("101A, 101B, 101C", "letter")
        self.cb_scheme.addItem("Building + unit", "building")
        self.cb_scheme.currentIndexChanged.connect(self._scheme)
        form.addRow("Unit numbers", self.cb_scheme)
        self.ed_building = QLineEdit()
        self.ed_building.setPlaceholderText("1")
        self.ed_building.setToolTip("Building id stored on every unit in this session. The unit number still changes.")
        self.ed_building.textChanged.connect(self._refresh)
        self.lbl_building = QLabel("Building")
        form.addRow(self.lbl_building, self.ed_building)
        self.ed_prefix = QLineEdit()
        self.ed_prefix.setPlaceholderText("Unit ")
        self.ed_prefix.setToolTip("Text before the unit number. Leave it empty for 1, 2, 3. Type Unit and a space for Unit 1.")
        self.ed_prefix.textChanged.connect(self._refresh)
        self.lbl_prefix = QLabel("Unit prefix")
        form.addRow(self.lbl_prefix, self.ed_prefix)
        self.sp_start = QSpinBox()
        self.sp_start.setRange(1, 9999)
        self.sp_start.setValue(1)
        self.sp_start.setToolTip("First unit number, or the first letter (1 = A). 101, 102, 103 starts at 101. "
                                 "The house number does not change.")
        self.sp_start.valueChanged.connect(self._refresh)
        form.addRow("Start at", self.sp_start)
        lay.addLayout(form)
        self.lbl_next = QLabel()
        self.lbl_next.setWordWrap(True)
        lay.addWidget(self.lbl_next)
        self.chk_dw = QCheckBox("Straight driveway for each unit")
        self.chk_dw.setToolTip("Off by default. Units under one roof share the access point.")
        lay.addWidget(self.chk_dw)
        self._scheme()
        self.resize(340, 260)

    def set_unit_types(self, pairs):
        current = self.cb_type.currentData()
        self.cb_type.clear()
        self.cb_type.addItem("", "")
        for code, label in pairs or ():
            self.cb_type.addItem(str(label), str(code))
        index = self.cb_type.findData(current)
        if index < 0 and self.cb_type.count() > 1:
            index = 1
        if index >= 0:
            self.cb_type.setCurrentIndex(index)

    def set_side_numbers(self, left, right):
        """Before the first unit: show both possible house numbers in the empty box."""
        bits = [f"L {left}" if left else "", f"R {right}" if right else ""]
        text = " / ".join(b for b in bits if b)
        self.sp_base.setSpecialValueText(f"{text} – click a unit" if text else "click the road")
        self.house_touched = False
        self.set_base(0)

    def begin(self, canvas, create_driveway, driveway_available, unit_types=()):
        self.index = 0
        self.house_touched = False
        self.sp_base.setSpecialValueText("click the road")
        self.set_unit_types(unit_types)
        self.set_base(0)
        self.chk_dw.setChecked(bool(create_driveway) and driveway_available)
        self.chk_dw.setEnabled(driveway_available)
        self._refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event):  # noqa: N802
        apply_panel_fill(self)
        super().showEvent(event)

    def end(self):
        self.hide()

    def closeEvent(self, event):  # noqa: N802
        event.ignore()
        self.hide()
        self.sessionEnd.emit()

    def note_placed(self):
        self.index += 1
        self.house_touched = True
        self._refresh()

    def _scheme_name(self):
        raw = self.cb_scheme.currentData() or "unit"
        return "unit" if raw == "unit101" else raw

    def values(self):
        scheme = self._scheme_name()
        number, suffix, unit = (0, "", "")
        if self.sp_base.value() > 0:
            number, suffix, unit = next_unit(scheme, self.index, self.sp_base.value(),
                                             self.ed_prefix.text(), self.sp_start.value())
        building = self.ed_building.text().strip() if scheme == "building" else ""
        shown = self._shown(scheme, number, suffix, unit)
        if building and self.sp_base.value() > 0:
            shown = f"{building} / {shown}"
        return dict(scheme=scheme, base=self.sp_base.value(),
                    prefix=self.ed_prefix.text(), start=self.sp_start.value(),
                    index=self.index, create_driveway=self.chk_dw.isChecked(),
                    unit_type=self.cb_type.currentData() or "", building=building, shown=shown)

    def _shown(self, scheme, number, suffix, unit):
        if self.sp_base.value() <= 0:
            return "Click the road"
        kind = self.cb_type.currentText().strip()
        if scheme == "letter":
            return f"{number}{suffix}"
        if scheme == "alpha":
            text = f"{number} {unit}".strip()
        else:
            text = f"{number} {unit}".strip()
        if kind and scheme != "letter":
            text = f"{number} {kind} {unit}".strip()
        return text

    def set_base(self, number):
        self._setting_base = True
        try:
            self.sp_base.setValue(int(number))
        finally:
            self._setting_base = False
        self._refresh()

    def _base_edited(self, *_args):
        if not self._setting_base:
            self.house_touched = True
        self._refresh()

    def _scheme(self, *_args):
        raw = self.cb_scheme.currentData()
        unit_numbers = raw in ("unit", "unit101")
        self.lbl_prefix.setVisible(unit_numbers)
        self.ed_prefix.setVisible(unit_numbers)
        self.lbl_building.setVisible(raw == "building")
        self.ed_building.setVisible(raw == "building")
        if raw == "unit101" and self.sp_start.value() < 100:
            self.sp_start.setValue(101)
        self._refresh()

    def _refresh(self, *_args):
        v = self.values()
        self.lbl_next.setText(f"Next: {v['shown']}    Placed: {self.index}")

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
        self._side_numbers = {}
        self._base_side = ""

    def _reset(self):
        super()._reset()
        self._side_numbers, self._base_side = {}, ""
        if getattr(self, "panel", None) is not None:
            self.panel.end()

    def canvasMoveEvent(self, event):  # noqa: N802
        if self.ctx is None:
            return
        pt = self._layer_pt(event, building=bool(self.points))
        if not self.points:
            self._hover_access(pt)
            return
        self._draw(pt)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if not self._ensure_context():
            return
        pt = self._layer_pt(event, building=bool(self.points))
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
                self.panel.begin(self.canvas(), False, self.ctx.dw is not None,
                                 unit_type_choices(self.ctx.pcfg))
                self._lock_house_number(pt)
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

    def _lock_house_number(self, _hint=None):
        """Work out the house number on both sides. The first unit click picks the side."""
        self._side_numbers, self._base_side = {}, ""
        try:
            numbers = E.house_numbers_by_side(self.ctx, self.segment_fid, self.points[0], self.parent_dw)
        except ConfigError as exc:
            self.notify("Warning", f"{exc} Type the house number in the Place units window.")
            return
        self._side_numbers = numbers
        self.panel.set_side_numbers(numbers.get("L"), numbers.get("R"))
        left, right = numbers.get("L"), numbers.get("R")
        shown = " and ".join(t for t in (f"{left} on the left" if left else "",
                                          f"{right} on the right" if right else "") if t)
        self.notify("Info", f"House number here: {shown or 'none'}. Click the first unit. "
                            "Its side of the road picks the house number, and every unit keeps it.")

    def _place(self, rooftop):
        access = self.points[0]
        try:
            plan = E.plan_address(self.ctx, access, rooftop, [], self.segment_fid,
                                  parent_driveway=self.parent_dw)
        except ConfigError as exc:
            self.notify("Warning", str(exc))
            return
        side_name = "left" if plan.side == "L" else "right"
        if not self.panel.house_touched and self._base_side != plan.side:
            number = self._side_numbers.get(plan.side)
            if not number and plan.result.number is not None and not plan.result.needs_choice:
                number = int(plan.result.number)
            if number:
                if self._base_side and self.panel.index:
                    self.notify("Warning", f"This unit is across the road from the last one, so it takes the "
                                           f"{side_name}-side house number {number}.")
                self.panel.set_base(number)
                self._base_side = plan.side
        v = self.panel.values()
        if v["base"] > 0 and not E.parity_matches(v["base"], plan.parity):
            want = "odd" if plan.parity == "O" else "even"
            self.notify("Warning", f"House number {v['base']} is not {want}, but this unit is on the {side_name} "
                                   f"side of the road, which takes {want} numbers. Check the number in the "
                                   "Place units window.")
        base = v["base"]
        if base <= 0:
            self.notify("Warning", "Type the house number in the Place units window, then click the unit again.")
            return
        number, suffix, unit = next_unit(v["scheme"], v["index"], base, v["prefix"], v["start"])
        try:
            ids = E.commit_address(self.ctx, plan, number, suffix, unit, LK.default_placement(self.ctx.pcfg),
                                   v["create_driveway"], unit_type=v["unit_type"], building=v["building"])
        except ConfigError as exc:
            self.notify("Warning", str(exc))
            return
        label = self.panel._shown(v["scheme"], number, suffix, unit)
        if v["building"]:
            label = f"{v['building']} / {label}"
        self.panel.note_placed()
        self.notify("Success", f"Address {label} {plan.full_name} created.")
        self._nguid_note(ids)
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
