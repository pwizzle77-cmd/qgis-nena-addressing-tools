# -*- coding: utf-8 -*-
"""
Small compatibility layer so the same code runs on QGIS 3.34/3.40 LTR (Qt5)
and QGIS 4 (Qt6). Keep every version-dependent API access in here.
"""

from qgis.core import Qgis, QgsField, QgsMapLayerProxyModel

try:  # Qt6 / QGIS 4
    from qgis.PyQt.QtGui import QAction  # noqa: F401
except ImportError:  # Qt5 / QGIS 3
    from qgis.PyQt.QtWidgets import QAction  # noqa: F401

try:
    from qgis.PyQt.QtCore import QVariant
except ImportError:  # pragma: no cover - PyQt6 without QVariant
    QVariant = None

QGIS_VERSION_INT = Qgis.QGIS_VERSION_INT


def is_null(value) -> bool:
    """True for None and for a null QVariant (QGIS 3 returns QVariant NULL for empty attributes)."""
    if value is None:
        return True
    if QVariant is not None and isinstance(value, QVariant):
        return value.isNull()
    return False


def py_value(value):
    """Return a plain Python value (None for NULL)."""
    if is_null(value):
        return None
    if QVariant is not None and isinstance(value, QVariant):
        return value.value()
    return value


def tell(progress, pct, text=None):
    """Report a percent and, when the callback accepts it, what is happening now.

    A callback that only takes the percent still works.
    """
    if progress is None:
        return
    try:
        progress(pct, text)
    except TypeError:
        progress(pct)


def line_layer_filter():
    """Filter flag for QgsMapLayerComboBox limited to line layers."""
    lf = getattr(Qgis, "LayerFilter", None)          # QGIS >= 3.34
    if lf is not None:
        return lf.LineLayer
    return QgsMapLayerProxyModel.LineLayer


def point_layer_filter():
    lf = getattr(Qgis, "LayerFilter", None)
    if lf is not None:
        return lf.PointLayer
    return QgsMapLayerProxyModel.PointLayer


def make_field(name: str, kind: str, length: int = 0) -> QgsField:
    """
    Create a QgsField of a logical kind ('string', 'int', 'datetime', 'double').
    QGIS >= 3.38 prefers QMetaType; older versions need QVariant types.
    """
    if QGIS_VERSION_INT >= 33800:
        from qgis.PyQt.QtCore import QMetaType
        types = {
            "string": QMetaType.Type.QString,
            "int": QMetaType.Type.Int,
            "datetime": QMetaType.Type.QDateTime,
            "double": QMetaType.Type.Double,
        }
    else:
        types = {
            "string": QVariant.String,
            "int": QVariant.Int,
            "datetime": QVariant.DateTime,
            "double": QVariant.Double,
        }
    fld = QgsField(name, types[kind])
    if kind == "string" and length:
        fld.setLength(length)
    return fld


def set_split_policy(layer, field_index: int, policy_name: str) -> bool:
    """
    Set a field split policy ('Duplicate', 'DefaultValue', 'GeometryRatio', 'UnsetField').
    Returns False if not supported by this QGIS version.
    """
    enum = getattr(Qgis, "FieldDomainSplitPolicy", None)
    if enum is None or not hasattr(layer, "setFieldSplitPolicy"):
        return False
    policy = getattr(enum, policy_name, None)
    if policy is None:
        policy = getattr(enum, "DefaultValue")
    layer.setFieldSplitPolicy(field_index, policy)
    return True


def geometry_type_line():
    return Qgis.GeometryType.Line


def msg_level(name: str):
    return getattr(Qgis.MessageLevel, name)


def table_layer_filter():
    """Filter flag for QgsMapLayerComboBox limited to tables without geometry."""
    lf = getattr(Qgis, "LayerFilter", None)
    if lf is not None:
        return lf.NoGeometry
    return QgsMapLayerProxyModel.NoGeometry
