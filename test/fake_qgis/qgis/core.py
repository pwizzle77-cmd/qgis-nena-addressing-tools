# -*- coding: utf-8 -*-
"""
Minimal in-memory stand-in for the parts of qgis.core used by the plugin's
core engines. ONLY for running the integration tests without a QGIS
install – it mimics behaviour (planar maths, edit commands, signals), not
the full API.
"""

import copy
import math
from types import SimpleNamespace

from .PyQt.QtCore import Signal, QVariant


class _Enum(SimpleNamespace):
    pass


class QgsVectorDataProvider:
    class Capability:
        AddFeatures = 1


class QgsFeatureSink:
    class Flag:
        FastInsert = 1


class Qgis:
    QGIS_VERSION_INT = 34000
    DistanceUnit = _Enum(Meters="m", Degrees="deg", Feet="ft")
    MessageLevel = _Enum(Info=0, Warning=1, Critical=2, Success=3)
    GeometryType = _Enum(Line=1, Point=0)
    LayerFilter = _Enum(LineLayer=4, PointLayer=2)
    FieldDomainSplitPolicy = _Enum(DefaultValue=0, Duplicate=1, GeometryRatio=2, UnsetField=3)
    WkbType = _Enum(NoGeometry=100)


class QgsMapLayerProxyModel:
    LineLayer = 4
    PointLayer = 2


class QgsMessageLog:
    @staticmethod
    def logMessage(*a, **k):
        print("LOG:", a)


class QgsPointXY:
    def __init__(self, x=0.0, y=None):
        if y is None and hasattr(x, "x"):
            x, y = x.x(), x.y()
        self._x, self._y = float(x), float(y)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def __repr__(self):
        return f"P({self._x},{self._y})"


class QgsRectangle:
    def __init__(self, *a):
        if len(a) == 1 and isinstance(a[0], QgsRectangle):
            a = a[0].v
        self.v = list(a) if len(a) == 4 else [0.0, 0.0, 0.0, 0.0]

    def scale(self, f):
        pass

    def combineExtentWith(self, o):
        self.v = [min(self.v[0], o.v[0]), min(self.v[1], o.v[1]), max(self.v[2], o.v[2]), max(self.v[3], o.v[3])]

    def grow(self, d):
        self.v = [self.v[0] - d, self.v[1] - d, self.v[2] + d, self.v[3] + d]

    def buffered(self, d):
        r = QgsRectangle(self)
        r.grow(d)
        return r

    def intersects(self, o):
        return not (o.v[0] > self.v[2] or o.v[2] < self.v[0] or o.v[1] > self.v[3] or o.v[3] < self.v[1])


class QgsWkbTypes:
    @staticmethod
    def isCurvedType(t):
        return False

    @staticmethod
    def isMultiType(t):
        return False

    @staticmethod
    def displayString(t):
        return "LineString"


class QgsGeometry:
    def __init__(self, other=None):
        self.kind = None
        self.pts = []
        if isinstance(other, QgsGeometry):
            self.kind, self.pts = other.kind, [QgsPointXY(p) for p in other.pts]

    @staticmethod
    def fromPolylineXY(pts):
        g = QgsGeometry()
        g.kind, g.pts = "line", [QgsPointXY(p) for p in pts]
        return g

    @staticmethod
    def fromPolygonXY(rings):
        g = QgsGeometry()
        g.kind, g.pts = "polygon", [QgsPointXY(p) for p in rings[0]]
        return g

    def contains(self, other):
        if self.kind != "polygon":
            return False
        x, y = other.pts[0].x(), other.pts[0].y()
        inside, pts = False, self.pts
        for i in range(len(pts)):
            a, b = pts[i], pts[i - 1]
            if (a.y() > y) != (b.y() > y) and x < (b.x() - a.x()) * (y - a.y()) / (b.y() - a.y()) + a.x():
                inside = not inside
        return inside

    def vertexAt(self, i):
        return QgsPointXY(self.pts[max(0, min(i, len(self.pts) - 1))])

    def interpolate(self, d):
        acc = 0.0
        for a, b in zip(self.pts, self.pts[1:]):
            seg = math.dist((a.x(), a.y()), (b.x(), b.y()))
            if acc + seg >= d and seg > 0:
                t = (d - acc) / seg
                return QgsGeometry.fromPointXY(QgsPointXY(a.x() + t * (b.x() - a.x()), a.y() + t * (b.y() - a.y())))
            acc += seg
        return QgsGeometry.fromPointXY(self.pts[-1])

    @staticmethod
    def fromPointXY(p):
        g = QgsGeometry()
        g.kind, g.pts = "point", [QgsPointXY(p)]
        return g

    def isNull(self):
        return self.kind is None

    def isEmpty(self):
        return not self.pts

    def isMultipart(self):
        return False

    def wkbType(self):
        return 2

    def asPolyline(self):
        return list(self.pts)

    def asPoint(self):
        return self.pts[0]

    def centroid(self):
        return self

    def transform(self, xf):
        return 0

    def isGeosValid(self):
        return True

    def type(self):
        return Qgis.GeometryType.Point if self.kind == "point" else Qgis.GeometryType.Line

    def asGeometryCollection(self):
        if self.kind == "multipoint":
            return [QgsGeometry.fromPointXY(p) for p in self.pts]
        return [QgsGeometry(self)] if not self.isNull() else []

    def intersection(self, other):
        out = []
        for a, b in zip(self.pts, self.pts[1:]):
            for c, d in zip(other.pts, other.pts[1:]):
                r = (b.x() - a.x(), b.y() - a.y())
                q = (d.x() - c.x(), d.y() - c.y())
                den = r[0] * q[1] - r[1] * q[0]
                if abs(den) < 1e-12:
                    continue
                t = ((c.x() - a.x()) * q[1] - (c.y() - a.y()) * q[0]) / den
                u = ((c.x() - a.x()) * r[1] - (c.y() - a.y()) * r[0]) / den
                if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                    p = QgsPointXY(a.x() + t * r[0], a.y() + t * r[1])
                    if not any(math.dist((p.x(), p.y()), (o.x(), o.y())) < 1e-9 for o in out):
                        out.append(p)
        g = QgsGeometry()
        if out:
            g.kind, g.pts = "multipoint", out
        return g

    def length(self):
        return sum(math.dist((a.x(), a.y()), (b.x(), b.y())) for a, b in zip(self.pts, self.pts[1:]))

    def boundingBox(self):
        xs = [p.x() for p in self.pts]
        ys = [p.y() for p in self.pts]
        return QgsRectangle(min(xs), min(ys), max(xs), max(ys))

    def mergeLines(self):
        return QgsGeometry(self)

    def convertToMultiType(self):
        return True

    def constGet(self):
        return _Curve(self)

    def lineLocatePoint(self, pg):
        pt = pg.asPoint()
        _d2, closest, after, _l = self.closestSegmentWithContext(pt)
        acc = 0.0
        for a, b in zip(self.pts[:after - 1], self.pts[1:after]):
            acc += math.dist((a.x(), a.y()), (b.x(), b.y()))
        p = self.pts[after - 1]
        return acc + math.dist((p.x(), p.y()), (closest.x(), closest.y()))

    def closestSegmentWithContext(self, pt):
        best = None
        for i, (a, b) in enumerate(zip(self.pts, self.pts[1:])):
            ax, ay, bx, by = a.x(), a.y(), b.x(), b.y()
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy or 1e-12
            t = max(0.0, min(1.0, ((pt.x() - ax) * dx + (pt.y() - ay) * dy) / L2))
            cx, cy = ax + t * dx, ay + t * dy
            d2 = (pt.x() - cx) ** 2 + (pt.y() - cy) ** 2
            cross = dx * (pt.y() - ay) - dy * (pt.x() - ax)
            left = -1 if cross > 0 else (1 if cross < 0 else 0)
            if best is None or d2 < best[0]:
                best = (d2, QgsPointXY(cx, cy), i + 1, left)
        return best


class _Curve:
    def __init__(self, g):
        self.g = g

    def length(self):
        return self.g.length()

    def numGeometries(self):
        return 1

    def curveSubstring(self, d0, d1):
        pts, acc, out = self.g.pts, 0.0, []
        for a, b in zip(pts, pts[1:]):
            seg = math.dist((a.x(), a.y()), (b.x(), b.y()))
            for d in (d0, d1):
                if acc <= d <= acc + seg and seg > 0:
                    t = (d - acc) / seg
                    q = QgsPointXY(a.x() + t * (b.x() - a.x()), a.y() + t * (b.y() - a.y()))
                    if not out or (out[-1].x(), out[-1].y()) != (q.x(), q.y()):
                        out.append(q)
            if d0 < acc + seg < d1:
                out.append(QgsPointXY(b))
            acc += seg
        return QgsGeometry.fromPolylineXY(out)


class QgsVectorLayerUtils:
    @staticmethod
    def createFeature(layer, geom, attrs, ctx=None):
        f = QgsFeature()
        vals = [None] * len(layer.fields())
        for i, v in attrs.items():
            vals[i] = v
        f.setAttributes(vals)
        f.setGeometry(geom)
        return f


class QgsDistanceArea:
    def setSourceCrs(self, *a):
        pass

    def setEllipsoid(self, e):
        return True

    def measureLength(self, g):
        return g.length()

    def convertLengthMeasurement(self, v, unit):
        return v


class QgsUnitTypes:
    @staticmethod
    def fromUnitToUnitFactor(a, b):
        return 1.0


class QgsCoordinateReferenceSystem:
    def __init__(self, authid="EPSG:3857"):
        self._a = authid

    def isGeographic(self):
        return False

    def mapUnits(self):
        return "m"

    def ellipsoidAcronym(self):
        return "EPSG:7030"

    def authid(self):
        return self._a

    def toWkt(self):
        return ""


class QgsCoordinateTransform:
    def __init__(self, *a):
        pass

    def transform(self, p):
        return QgsPointXY(p)

    def transformBoundingBox(self, r):
        return r


class QgsField:
    def __init__(self, name, ftype="string"):
        self._n, self._t = name, ftype

    def name(self):
        return self._n

    def typeName(self):
        return {"int": "integer", "string": "String", "datetime": "DateTime", "double": "double"}.get(self._t, "String")

    def isNumeric(self):
        return self._t in ("int", "double")

    def setLength(self, n):
        self._len = n

    def length(self):
        return getattr(self, "_len", 0)

    def comment(self):
        return ""

    def alias(self):
        return ""


class QgsFields:
    def __init__(self, fl=None):
        self._f = list(fl or [])

    def append(self, f):
        self._f.append(f)

    def toList(self):
        return list(self._f)

    def lookupField(self, name):
        for i, f in enumerate(self._f):
            if f.name().lower() == (name or "").lower():
                return i
        return -1

    def at(self, i):
        return self._f[i]

    def field(self, i):
        return self.at(i)

    def __iter__(self):
        return iter(self._f)

    def __len__(self):
        return len(self._f)

    def count(self):
        return len(self._f)


class QgsFeature:
    def __init__(self, fields_or_id=None, *a):
        self._id = fields_or_id if isinstance(fields_or_id, int) else 0
        self._attrs = []
        self._geom = QgsGeometry()

    def id(self):
        return self._id

    def setId(self, i):
        self._id = i

    def attribute(self, i):
        v = self._attrs[i] if i < len(self._attrs) else None
        return QVariant() if v is None else v

    def attributes(self):
        return list(self._attrs)

    def setAttributes(self, a):
        self._attrs = list(a)

    def setAttribute(self, i, value):
        if i >= len(self._attrs):
            self._attrs.extend([None] * (i + 1 - len(self._attrs)))
        self._attrs[i] = value

    def geometry(self):
        return QgsGeometry(self._geom)

    def setGeometry(self, g):
        self._geom = QgsGeometry(g)

    def hasGeometry(self):
        return not self._geom.isNull()

    def isValid(self):
        return True


class QgsFeatureRequest:
    def __init__(self, fid=None):
        self.fids = None

    def setFilterFids(self, fids):
        self.fids = set(fids)
        return self

    def setFilterFid(self, fid):
        self.fids = {fid}
        return self

    def setNoAttributes(self):
        return self

    def setSubsetOfAttributes(self, a):
        return self

    def setFilterRect(self, r):
        self.rect = r
        return self

    def accepts(self, f):
        if self.fids is not None and f.id() not in self.fids:
            return False
        rect = getattr(self, "rect", None)
        if rect is not None and f.hasGeometry() and not f.geometry().boundingBox().intersects(rect):
            return False
        return True


class QgsSpatialIndex:
    def __init__(self):
        self.items = {}

    def addFeature(self, f):
        self.items[f.id()] = f.geometry()
        return True

    def nearestNeighbor(self, pt, n=1, maxDistance=0):
        scored = []
        for fid, g in self.items.items():
            d = math.sqrt(g.closestSegmentWithContext(pt)[0])
            if not maxDistance or d <= maxDistance:
                scored.append((d, fid))
        return [fid for _, fid in sorted(scored)[:n]]


class _Provider:
    def __init__(self, layer):
        self.layer = layer

    def getFeatures(self, request=None):
        for fid, f in sorted(self.layer._committed.items()):
            if request is None or request.accepts(f):
                yield copy.deepcopy(f)

    def addAttributes(self, flds):
        for f in flds:
            self.layer._fields.append(f)
        return True

    def addFeatures(self, feats):
        for f in feats:
            self.layer._next += 1
            f.setId(self.layer._next)
            self.layer._feats[f.id()] = f
        return True

    def capabilities(self):
        return QgsVectorDataProvider.Capability.AddFeatures


class QgsVectorLayer:
    _counter = 0

    def __init__(self, uri="", name="layer", provider="memory"):
        QgsVectorLayer._counter += 1
        self._id = f"layer_{QgsVectorLayer._counter}"
        self._name = name
        self._fields = QgsFields()
        self._feats = {}
        self._committed = {}
        self._next = 0
        self._new = 0
        self._editable = False
        self._sel = []
        self._props = {}
        self.split_policies = {}
        self.commands = []
        self.editCommandStarted = Signal()
        self.editCommandEnded = Signal()
        self.editCommandDestroyed = Signal()
        self.geometryChanged = Signal()
        self.featureAdded = Signal()
        self.featureDeleted = Signal()
        self.selectionChanged = Signal()
        self.attributeValueChanged = Signal()

    # identity
    def id(self):
        return self._id

    def name(self):
        return self._name

    def crs(self):
        return QgsCoordinateReferenceSystem()

    def source(self):
        return f"memory://{self._id}"

    def providerType(self):
        return "memory"

    def fields(self):
        return self._fields

    def updateFields(self):
        pass

    def dataProvider(self):
        return _Provider(self)

    def customProperty(self, k, d=None):
        return self._props.get(k, d)

    def setCustomProperty(self, k, v):
        self._props[k] = v

    def primaryKeyAttributes(self):
        return []

    def createExpressionContext(self):
        return None

    def wkbType(self):
        return getattr(self, "_wkb", 2)

    def isModified(self):
        return False

    def updateExtents(self):
        pass

    def reload(self):
        pass

    def setFieldSplitPolicy(self, idx, pol):
        self.split_policies[idx] = pol

    # data
    def add_committed(self, attrs, pts):
        f = QgsFeature()
        self._next += 1
        f.setId(self._next)
        f.setAttributes(list(attrs) + [None] * (len(self._fields) - len(attrs)))
        if pts:
            f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in pts]))
        self._feats[f.id()] = f
        self._committed[f.id()] = copy.deepcopy(f)
        return f.id()

    def getFeatures(self, request=None):
        for fid in sorted(self._feats):
            if request is None or request.accepts(self._feats[fid]):
                yield copy.deepcopy(self._feats[fid])

    def featureCount(self):
        return len(self._feats)

    def selectedFeatureIds(self):
        return list(self._sel)

    def selectByIds(self, ids):
        self._sel = list(ids)
        self.selectionChanged.emit()

    def getSelectedFeatures(self):
        return self.getFeatures(QgsFeatureRequest().setFilterFids(self._sel))

    # editing
    def isEditable(self):
        return self._editable

    def startEditing(self):
        self._editable = True
        return True

    def rollBack(self):
        self._editable = False

    def beginEditCommand(self, text):
        self.commands.append(text)
        self.editCommandStarted.emit(text)

    def endEditCommand(self):
        self.editCommandEnded.emit()

    def destroyEditCommand(self):
        self.editCommandDestroyed.emit()

    def changeAttributeValues(self, fid, new, old=None):
        f = self._feats[fid]
        attrs = f.attributes() + [None] * (len(self._fields) - len(f.attributes()))
        for i, v in new.items():
            attrs[i] = v
        f.setAttributes(attrs)
        for i, v in new.items():
            self.attributeValueChanged.emit(fid, i, v)
        return True

    def changeGeometry(self, fid, geom):
        self._feats[fid].setGeometry(geom)
        self.geometryChanged.emit(fid, geom)
        return True

    def addFeature(self, f):
        if len(f.attributes()) < len(self._fields):
            f.setAttributes(f.attributes() + [None] * (len(self._fields) - len(f.attributes())))
        self._new -= 1
        f.setId(self._new)
        self._feats[self._new] = f
        self.featureAdded.emit(self._new)
        return True

    def deleteFeature(self, fid):
        self._feats.pop(fid)
        self.featureDeleted.emit(fid)
        return True

    def addAttribute(self, fld):
        self._fields.append(fld)
        return True

    def triggerRepaint(self):
        pass


class QgsVectorFileWriter:
    class SaveVectorOptions:
        pass


class QgsProviderRegistry:
    @staticmethod
    def instance():
        return QgsProviderRegistry()

    def providerMetadata(self, key):
        return None


class QgsProject:
    _inst = None

    def __init__(self):
        self.entries = {}
        self.readProject = Signal()
        self.layers = {}
        self.layersAdded = Signal()
        self.layerWillBeRemoved = Signal()

    @classmethod
    def instance(cls):
        if cls._inst is None:
            cls._inst = QgsProject()
        return cls._inst

    def transformContext(self):
        return None

    def ellipsoid(self):
        return "EPSG:7030"

    def mapLayer(self, lid):
        return self.layers.get(lid)

    def mapLayers(self):
        return dict(self.layers)

    def addMapLayer(self, lyr):
        self.layers[lyr.id()] = lyr
        return lyr

    def setDirty(self, d):
        pass

    def writeEntry(self, scope, key, value):
        self.entries[(scope, key)] = value
        return True

    def readEntry(self, scope, key, default=""):
        if (scope, key) in self.entries:
            return self.entries[(scope, key)], True
        return default, False

    def crs(self):
        return QgsCoordinateReferenceSystem()
