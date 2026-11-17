# -*- coding: utf-8 -*-
"""
Fill WhoEntered and LastUpdatedBy from the database connection login.

A PostgreSQL, SQL Server, Oracle, or SAP HANA layer has a user on the QGIS
connection. On save, that login is written into an empty WhoEntered on new
features and into LastUpdatedBy on every new or changed feature. A file
layer has no connection login, so those columns are left alone. A WhoEntered
value already stored, including one copied onto a split piece, is kept.

The columns are found by name. They do not have to be in the field mapping.
"""

import re

from qgis.core import QgsFeatureRequest, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QObject

from ..compat import py_value
from .config import load_from_layer
from .project_config import layer_for, load_project_config
from .schema import LAST_UPDATED_BY, WHO_ENTERED, normalize_name
from .qt_safety import disconnect_all, safe_disconnect

# Provider keys that store a login on the QGIS connection. SpatiaLite and
# OGR files do not.
DB_LOGIN_PROVIDERS = ("postgres", "mssql", "oracle", "hana")

_USER_RE = re.compile(
    r"""(?:username|user|uid)\s*=\s*(?:'([^']*)'|"([^"]*)"|(\S+))""",
    re.IGNORECASE)
_AUTH_RE = re.compile(
    r"""authcfg\s*=\s*(?:'([^']*)'|"([^"]*)"|(\S+))""",
    re.IGNORECASE)


def uri_username(text: str) -> str:
    """Login stored in a provider URI, or '' when the URI has none."""
    text = text or ""
    parsed = ""
    try:
        from qgis.core import QgsDataSourceUri
        parsed = (QgsDataSourceUri(text).username() or "").strip()
    except Exception:  # noqa: BLE001 - a URI the parser does not understand
        parsed = ""
    if parsed:
        return parsed
    match = _USER_RE.search(text)
    if not match:
        return ""
    value = next((group for group in match.groups() if group), "")
    return value.replace("\\\\", "\\").strip()


def _uri_authcfg(text: str) -> str:
    text = text or ""
    try:
        from qgis.core import QgsDataSourceUri
        auth = (QgsDataSourceUri(text).authConfigId() or "").strip()
        if auth:
            return auth
    except Exception:  # noqa: BLE001
        pass
    match = _AUTH_RE.search(text)
    if not match:
        return ""
    return next((group for group in match.groups() if group), "").strip()


def _auth_username(authcfg: str) -> str:
    """Username stored on a QGIS authentication config, without decrypting the password."""
    authcfg = (authcfg or "").strip()
    if not authcfg:
        return ""
    try:
        from qgis.core import QgsApplication, QgsAuthMethodConfig
        manager = QgsApplication.authManager()
        if manager is None:
            return ""
        cfg = QgsAuthMethodConfig()
        if not manager.loadAuthenticationConfig(authcfg, cfg, False):
            return ""
        for key in ("username", "user", "uid"):
            try:
                value = cfg.config(key)
            except Exception:  # noqa: BLE001 - config key the method does not use
                value = ""
            value = str(value or "").strip()
            if value:
                return value
    except Exception:  # noqa: BLE001 - auth database locked or unavailable
        return ""
    return ""


def _source_text(layer) -> str:
    chunks = []
    try:
        chunks.append(layer.source() or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        provider = layer.dataProvider()
        uri = provider.uri() if provider is not None else None
        if uri is not None:
            chunks.append(uri.uri() if hasattr(uri, "uri") else str(uri))
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(chunk for chunk in chunks if chunk)


def _stored_username(provider: str, source: str) -> str:
    """Login of the saved QGIS connection that matches this layer's host and database."""
    try:
        from qgis.core import QgsDataSourceUri, QgsProviderRegistry
        layer_uri = QgsDataSourceUri(source)
        registry = QgsProviderRegistry.instance()
        metadata = registry.providerMetadata(provider) if registry is not None else None
    except Exception:  # noqa: BLE001
        return ""
    if metadata is None:
        return ""
    try:
        connections = metadata.connections() or {}
    except Exception:  # noqa: BLE001
        return ""
    host = (layer_uri.host() or "").strip().lower()
    database = (layer_uri.database() or "").strip().lower()
    port = str(layer_uri.port() or "")
    auth = (layer_uri.authConfigId() or "").strip()
    for conn in connections.values():
        try:
            text = conn.uri() if hasattr(conn, "uri") else ""
            stored = QgsDataSourceUri(text)
        except Exception:  # noqa: BLE001
            continue
        if database and (stored.database() or "").strip().lower() != database:
            continue
        if host and (stored.host() or "").strip().lower() != host:
            continue
        stored_port = str(stored.port() or "")
        if port and port != "0" and stored_port and stored_port != "0" and stored_port != port:
            continue
        stored_auth = (stored.authConfigId() or "").strip()
        if auth and stored_auth and stored_auth != auth:
            continue
        name = (stored.username() or "").strip() or _auth_username(stored_auth)
        if name:
            return name
    return ""


def connection_login(layer) -> str:
    """Username on the layer's database connection, or '' for a file layer."""
    if layer is None:
        return ""
    try:
        provider = (layer.providerType() or "").lower()
    except Exception:  # noqa: BLE001
        return ""
    if provider not in DB_LOGIN_PROVIDERS:
        return ""
    source = _source_text(layer)
    user = uri_username(source)
    if user:
        return user
    user = _auth_username(_uri_authcfg(source))
    if user:
        return user
    return _stored_username(provider, source)


def _column_index(layer, definition) -> int:
    wanted = {normalize_name(definition.name)}
    wanted.update(normalize_name(alias) for alias in definition.aliases)
    fields = layer.fields()
    for index in range(fields.count()):
        if normalize_name(fields.at(index).name()) in wanted:
            return index
    return -1


def _fit(layer, index: int, login: str) -> str:
    text = (login or "").strip()
    try:
        length = int(layer.fields().at(index).length() or 0)
    except (TypeError, ValueError):
        length = 0
    if length > 0 and len(text) > length:
        text = text[:length]
    return text


def _blank(value) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _ids(value) -> set:
    if not value:
        return set()
    try:
        return set(value.keys())
    except AttributeError:
        try:
            return set(value)
        except TypeError:
            return set()


def _feature(layer, fid):
    getter = getattr(layer, "getFeature", None)
    if getter is not None:
        try:
            feat = getter(fid)
            if feat is not None and feat.isValid():
                return feat
        except Exception:  # noqa: BLE001
            pass
    try:
        return next(layer.getFeatures(QgsFeatureRequest().setFilterFid(int(fid))), None)
    except (TypeError, ValueError, StopIteration):
        return None


def _current(feat, index: int):
    if feat is None or index < 0:
        return None
    try:
        return py_value(feat.attribute(index))
    except Exception:  # noqa: BLE001
        return None


def stamp_editor_user(layer) -> int:
    """
    Write the connection login into the open edit buffer.

    Returns the number of features updated. Does nothing when the layer is
    not a database layer, the connection has no login, or neither column exists.
    """
    if layer is None:
        return 0
    try:
        if not layer.isModified():
            return 0
    except Exception:  # noqa: BLE001
        return 0
    login = connection_login(layer)
    if not login:
        return 0
    who_index = _column_index(layer, WHO_ENTERED)
    updated_index = _column_index(layer, LAST_UPDATED_BY)
    if who_index < 0 and updated_index < 0:
        return 0
    getter = getattr(layer, "editBuffer", None)
    buffer = getter() if getter else None
    if buffer is None:
        return 0
    deleted = _ids(buffer.deletedFeatureIds())
    added = _ids(buffer.addedFeatures()) - deleted
    changed = _ids(buffer.changedAttributeValues())
    if hasattr(buffer, "changedGeometries"):
        changed |= _ids(buffer.changedGeometries())
    changed = (changed - deleted) - added
    who_value = _fit(layer, who_index, login) if who_index >= 0 else ""
    updated_value = _fit(layer, updated_index, login) if updated_index >= 0 else ""
    count = 0
    for fid, is_new in [(fid, True) for fid in added] + [(fid, False) for fid in changed]:
        feat = _feature(layer, fid)
        values = {}
        if is_new and who_index >= 0 and who_value and _blank(_current(feat, who_index)):
            values[who_index] = who_value
        if updated_index >= 0 and updated_value and _current(feat, updated_index) != updated_value:
            values[updated_index] = updated_value
        if not values:
            continue
        if layer.changeAttributeValues(fid, values):
            count += 1
    return count


def _live_layers() -> dict:
    """Centerlines, street names, addresses, and driveways this plugin edits."""
    found = {}
    pcfg = load_project_config()
    for settings, role in (
            (pcfg.streets, "centerline"),
            (pcfg.streets, "master"),
            (pcfg.streets, "address"),
            (pcfg.addresses, "driveway"),
            (pcfg.addresses, "access")):
        layer = layer_for(settings, role)
        if layer is not None:
            found[layer.id()] = layer
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer) and load_from_layer(layer) is not None:
            found[layer.id()] = layer
    return found


class EditorUserWatcher(QObject):
    """Stamp the connection login just before a live layer saves."""

    def __init__(self, notify):
        super().__init__()
        self.notify = notify
        self._conns = []
        self._layers = {}
        project = QgsProject.instance()
        project.readProject.connect(self.refresh)
        project.layersAdded.connect(self.refresh)
        removed = getattr(project, "layersRemoved", None)
        if removed is not None and hasattr(removed, "connect"):
            removed.connect(self.refresh)
        self.refresh()

    def unload(self):
        project = QgsProject.instance()
        for signal in (project.readProject, project.layersAdded, getattr(project, "layersRemoved", None)):
            if signal is None:
                continue
            safe_disconnect(project, signal, self.refresh)
        self._detach()

    def _detach(self):
        disconnect_all(self._conns)
        self._conns = []
        self._layers = {}

    def refresh(self, *_args):
        self._detach()
        self._layers = _live_layers()
        for layer_id, layer in self._layers.items():
            signal = getattr(layer, "beforeCommitChanges", None)
            if signal is None or not hasattr(signal, "connect"):
                continue
            slot = lambda *_, lid=layer_id: self._before(lid)
            signal.connect(slot)
            self._conns.append((layer, signal, slot))

    def _before(self, layer_id):
        layer = self._layers.get(layer_id)
        if layer is None:
            return
        try:
            stamp_editor_user(layer)
        except Exception as exc:  # noqa: BLE001 - a stamp must not block the save
            self.notify("Warning", f"The database login was not written: {exc}")
