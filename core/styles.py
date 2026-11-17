# -*- coding: utf-8 -*-
"""
Map styles for the NENA layers.

* **Road centerlines** look like roads: a grey casing with a white fill.
  Blue arrows along the line show the direction it was drawn (the direction
  the house numbers go up). A green dot marks the first point (From) and a
  red square the last point (To). Zoomed in, the street name runs along the
  road, and the From and To numbers for each side sit at each end. One-way
  roads (OneWay FT or TF) show larger red arrows in the direction of travel
  instead of the blue ones.
* **Address points** are small orange houses with the house number and unit.
* **Driveways** are brown dashed lines. **Access points** are blue diamonds.

The style is added as a named style, "NENA", in each layer's style manager.
The layer's previous style stays under its own name, so you can switch back
from the Layers panel (right-click → Styles).
"""

import base64
from typing import Dict, List, Optional

NENA_STYLE = "NENA"
STYLED_PROPERTY = "nena_addressing/styled"
STYLE_VERSION = "1"

# Colours
ROAD_CASING = "110,110,110"
ROAD_FILL = "255,255,255"
ARROW = "30,110,220"
START = "46,125,50"
END = "198,40,40"
ADDRESS = "230,81,0"
DRIVEWAY = "141,110,99"
ACCESS = "30,110,220"
ONE_WAY = "211,47,47"

_HOUSE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">'
    '<path d="M10 1.8 L18.4 9.2 H15.6 V18.2 H4.4 V9.2 H1.6 Z" fill="#e65100" stroke="#ffffff" '
    'stroke-width="1.5" stroke-linejoin="round"/></svg>'
)


# ---------------------------------------------------------------------------
# Label expressions (plain Python, testable)
# ---------------------------------------------------------------------------
def quote_field(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def full_name_expression(fields: Dict[str, str]) -> str:
    """Expression for the road name: FullName when there is one, else the name parts joined."""
    if fields.get("FullName"):
        parts = [quote_field(fields["FullName"])]
        built = _parts_expression(fields)
        return f"coalesce(nullif(trim({parts[0]}), ''), {built})" if built != "''" else parts[0]
    return _parts_expression(fields)


def _parts_expression(fields: Dict[str, str]) -> str:
    order = ("St_PreMod", "St_PreDir", "St_PreTyp", "St_PreSep", "St_Name", "St_PosTyp", "St_PosDir", "St_PosMod")
    cols = [quote_field(fields[name]) for name in order if fields.get(name)]
    if not cols:
        return "''"
    joined = " || ' ' || ".join(f"coalesce({col}, '')" for col in cols)
    return f"trim(regexp_replace({joined}, '\\\\s+', ' '))"


def range_expression(fields: Dict[str, str], end: str) -> Optional[str]:
    """'From  L 101  R 100' at the start, 'To  L 199  R 198' at the end. None when the fields are missing."""
    left = fields.get(f"{end}Addr_L")
    right = fields.get(f"{end}Addr_R")
    if not left and not right:
        return None
    word = "From" if end == "From" else "To"
    bits = [f"'{word}'"]
    if left:
        bits.append(f"'  L ' || coalesce(to_string({quote_field(left)}), '–')")
    if right:
        bits.append(f"'  R ' || coalesce(to_string({quote_field(right)}), '–')")
    return " || ".join(bits)


def one_way_expressions(fields: Dict[str, str]):
    """(is one-way, arrow angle) expressions for the OneWay field, or None when there is no such field."""
    name = fields.get("OneWay")
    if not name:
        return None
    value = f"upper(trim(coalesce({quote_field(name)}, '')))"
    return f"{value} IN ('FT', 'TF')", f"if({value} = 'TF', 180, 0)"


def address_label_expression(fields: Dict[str, str]) -> Optional[str]:
    """House number, suffix, and unit: '101A #2'."""
    number = fields.get("Add_Number")
    if not number:
        return None
    pieces = [quote_field(number)]
    if fields.get("AddNum_Suf"):
        pieces.append(quote_field(fields["AddNum_Suf"]))
    text = "concat(" + ", ".join(pieces) + ")"
    if fields.get("Unit"):
        unit = quote_field(fields["Unit"])
        text = f"{text} || if(coalesce({unit}, '') <> '', ' #' || {unit}, '')"
    return text


# ---------------------------------------------------------------------------
# QGIS helpers
# ---------------------------------------------------------------------------
def _enum(scoped: str, member: str, legacy_owner=None, legacy_member: str = ""):
    """``Qgis.<scoped>.<member>`` on newer QGIS, ``<legacy_owner>.<legacy_member>`` on older."""
    from qgis.core import Qgis
    group = getattr(Qgis, scoped, None)
    if group is not None and hasattr(group, member):
        return getattr(group, member)
    return getattr(legacy_owner, legacy_member or member)


def _marker_line(placement: str, marker: dict, interval: float = 0.0):
    from qgis.core import QgsMarkerLineSymbolLayer, QgsMarkerSymbol
    props = {
        "placement": placement.lower(),
        "placements": {"interval": "Interval", "firstvertex": "FirstVertex",
                       "lastvertex": "LastVertex"}[placement.lower()],
        "rotate": "1",
    }
    if interval:
        props["interval"] = str(interval)
        props["interval_unit"] = "MM"
        props["offset_along_line"] = str(interval / 2)
        props["offset_along_line_unit"] = "MM"
    layer = QgsMarkerLineSymbolLayer.create(props)
    layer.setSubSymbol(QgsMarkerSymbol.createSimple(marker))
    return layer


def _line(color: str, width: float, style: str = "solid"):
    from qgis.core import QgsSimpleLineSymbolLayer
    return QgsSimpleLineSymbolLayer.create({
        "line_color": color, "line_width": str(width), "line_width_unit": "MM",
        "line_style": style, "capstyle": "round", "joinstyle": "round",
    })


def _text_format(size: float, color: str, bold: bool = False):
    from qgis.core import QgsTextBufferSettings, QgsTextFormat
    from qgis.PyQt.QtGui import QColor, QFont
    fmt = QgsTextFormat()
    font = QFont()
    font.setBold(bold)
    fmt.setFont(font)
    fmt.setSize(size)
    fmt.setColor(QColor(*[int(v) for v in color.split(",")]))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    buffer.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buffer)
    return fmt


def _label(expression: str, fmt, placement: str, distance: float = 0.0, at: str = ""):
    from qgis.core import QgsPalLayerSettings, QgsWkbTypes
    settings = QgsPalLayerSettings()
    settings.enabled = True
    settings.isExpression = True
    settings.fieldName = expression
    settings.setFormat(fmt)
    settings.placement = _enum("LabelPlacement", placement, QgsPalLayerSettings)
    if distance:
        settings.dist = distance
    if at:
        settings.geometryGeneratorEnabled = True
        settings.geometryGenerator = f"{at}($geometry)"
        settings.geometryGeneratorType = _enum("GeometryType", "Point", QgsWkbTypes, "PointGeometry")
    return settings


def _rule(settings, description: str, max_scale_denominator: float):
    from qgis.core import QgsRuleBasedLabeling
    rule = QgsRuleBasedLabeling.Rule(settings)
    rule.setDescription(description)
    if max_scale_denominator:
        rule.setMinimumScale(max_scale_denominator)   # most zoomed-out scale it still shows at
        rule.setMaximumScale(0)
    return rule


def _enable_when(symbol_layer, expression: str) -> None:
    """Draw ``symbol_layer`` only for features where ``expression`` is true."""
    from qgis.core import QgsProperty, QgsSymbolLayer
    group = getattr(QgsSymbolLayer, "Property", QgsSymbolLayer)
    key = getattr(group, "LayerEnabled", None)
    if key is None:
        key = getattr(group, "PropertyLayerEnabled", None)
    if key is None:
        key = getattr(QgsSymbolLayer, "PropertyLayerEnabled", None)
    if key is None:
        return
    symbol_layer.setDataDefinedProperty(key, QgsProperty.fromExpression(expression))


def _use_nena_style(layer):
    """Switch the layer to a style named NENA, keeping the previous one under its own name."""
    try:
        manager = layer.styleManager()
        if manager.currentStyle() != NENA_STYLE:
            if NENA_STYLE not in manager.styles():
                manager.addStyleFromLayer(NENA_STYLE)
            manager.setCurrentStyle(NENA_STYLE)
    except Exception:  # noqa: BLE001 - styling still works without the style manager
        pass


def _finish(layer, renderer, rules):
    from qgis.core import QgsRuleBasedLabeling
    layer.setRenderer(renderer)
    if rules:
        root = QgsRuleBasedLabeling.Rule(None)
        for rule in rules:
            root.appendChild(rule)
        layer.setLabeling(QgsRuleBasedLabeling(root))
        layer.setLabelsEnabled(True)
    layer.setCustomProperty(STYLED_PROPERTY, STYLE_VERSION)
    layer.triggerRepaint()


# ---------------------------------------------------------------------------
# Layer styles
# ---------------------------------------------------------------------------
def style_centerline(layer, fields: Dict[str, str]) -> None:
    from qgis.core import QgsLineSymbol, QgsSingleSymbolRenderer
    _use_nena_style(layer)
    symbol = QgsLineSymbol()
    symbol.deleteSymbolLayer(0)
    symbol.appendSymbolLayer(_line(ROAD_CASING, 1.9))
    symbol.appendSymbolLayer(_line(ROAD_FILL, 1.25))
    digitized = _marker_line("interval", {
        "name": "filled_arrowhead", "color": ARROW, "outline_color": ARROW, "size": "2.0", "angle": "0"},
        interval=30)
    one_way = one_way_expressions(fields)
    if one_way:
        _enable_when(digitized, f"NOT ({one_way[0]})")
    symbol.appendSymbolLayer(digitized)
    if one_way:
        travel = _marker_line("interval", {
            "name": "filled_arrowhead", "color": ONE_WAY, "outline_color": "255,255,255",
            "outline_width": "0.3", "size": "3.4", "angle": "0"}, interval=22)
        _enable_when(travel, one_way[0])
        try:
            from qgis.core import QgsProperty
            travel.subSymbol().setDataDefinedAngle(QgsProperty.fromExpression(one_way[1]))
        except Exception:  # noqa: BLE001 - arrows still show, pointing along the line
            pass
        symbol.appendSymbolLayer(travel)
    symbol.appendSymbolLayer(_marker_line("firstvertex", {
        "name": "circle", "color": START, "outline_color": "255,255,255", "outline_width": "0.4", "size": "2.8"}))
    symbol.appendSymbolLayer(_marker_line("lastvertex", {
        "name": "square", "color": END, "outline_color": "255,255,255", "outline_width": "0.4", "size": "2.5"}))
    rules = []
    name = full_name_expression(fields)
    if name != "''":
        rules.append(_rule(_label(name, _text_format(8.5, "40,40,40"), "Line"), "Street name", 25000))
    for end, color, at in (("From", START, "start_point"), ("To", END, "end_point")):
        text = range_expression(fields, end)
        if text:
            rules.append(_rule(_label(text, _text_format(7.5, color, bold=True), "AroundPoint", 1.8, at),
                               f"{end} numbers", 5000))
    _finish(layer, QgsSingleSymbolRenderer(symbol), rules)


def style_address(layer, fields: Dict[str, str]) -> None:
    from qgis.core import QgsMarkerSymbol, QgsSingleSymbolRenderer, QgsSvgMarkerSymbolLayer
    _use_nena_style(layer)
    symbol = QgsMarkerSymbol.createSimple({"name": "circle", "color": ADDRESS, "outline_color": "255,255,255",
                                           "outline_width": "0.4", "size": "2.6"})
    try:
        encoded = base64.b64encode(_HOUSE_SVG.encode("utf-8")).decode("ascii")
        house = QgsSvgMarkerSymbolLayer.create({"name": "base64:" + encoded, "size": "4.2", "size_unit": "MM"})
        if house is not None:
            symbol.changeSymbolLayer(0, house)
    except Exception:  # noqa: BLE001 - the orange dot stays
        pass
    rules = []
    text = address_label_expression(fields)
    if text:
        rules.append(_rule(_label(text, _text_format(8, "120,40,0", bold=True), "AroundPoint", 1.5),
                           "House number", 5000))
    _finish(layer, QgsSingleSymbolRenderer(symbol), rules)


def style_driveway(layer) -> None:
    from qgis.core import QgsLineSymbol, QgsSingleSymbolRenderer
    _use_nena_style(layer)
    symbol = QgsLineSymbol()
    symbol.deleteSymbolLayer(0)
    symbol.appendSymbolLayer(_line(DRIVEWAY, 0.7, "dash"))
    _finish(layer, QgsSingleSymbolRenderer(symbol), [])


def style_access(layer) -> None:
    from qgis.core import QgsMarkerSymbol, QgsSingleSymbolRenderer
    _use_nena_style(layer)
    symbol = QgsMarkerSymbol.createSimple({"name": "diamond", "color": ACCESS, "outline_color": "255,255,255",
                                           "outline_width": "0.3", "size": "2.4"})
    _finish(layer, QgsSingleSymbolRenderer(symbol), [])


def nena_layers(pcfg, centerline=None) -> Dict[str, object]:
    """The layers that get a NENA style: centerline, address, driveway, access."""
    from .lookups import feature_layers
    from .project_config import layer_for
    layers = feature_layers(pcfg, centerline)
    return {
        "centerline": layers.get("centerline"),
        "address": layers.get("address"),
        "driveway": layer_for(pcfg.addresses, "driveway"),
        "access": layer_for(pcfg.addresses, "access"),
    }


def apply_styles(pcfg, centerline=None, only: Optional[Dict[str, object]] = None) -> List[str]:
    """Style the NENA layers. ``only`` limits it to ``{key: layer}``. Returns the names styled."""
    from .lookups import feature_layers, field_maps
    layers = only if only is not None else nena_layers(pcfg, centerline)
    maps = field_maps(pcfg, feature_layers(pcfg, layers.get("centerline") or centerline))
    if layers.get("centerline") is not None and "centerline" not in maps:
        from .lookups import resolve_fields
        from .schema import RCL_FIELDS
        lyr = layers["centerline"]
        maps["centerline"] = resolve_fields([f.name() for f in lyr.fields()], RCL_FIELDS, {})
    if layers.get("address") is not None and "address" not in maps:
        from .addressing.ssap import SSAP_FIELDS
        from .lookups import resolve_fields
        lyr = layers["address"]
        maps["address"] = resolve_fields([f.name() for f in lyr.fields()], SSAP_FIELDS, {})
    done = []
    for key, layer in layers.items():
        if layer is None:
            continue
        if key == "centerline":
            style_centerline(layer, maps.get("centerline", {}))
        elif key == "address":
            style_address(layer, maps.get("address", {}))
        elif key == "driveway":
            style_driveway(layer)
        elif key == "access":
            style_access(layer)
        else:
            continue
        done.append(layer.name())
    return done
