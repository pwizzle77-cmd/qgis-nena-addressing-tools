# -*- coding: utf-8 -*-
"""
Disconnect signals without touching Qt objects that are already gone.

When QGIS closes, or a layer is removed from the project, the C++ layer is
deleted before the plugin lets go of it. Qt drops every connection of a
deleted object by itself, but calling ``disconnect`` through a stale Python
handle reads freed memory: on Qt 6 that is an access violation, not a Python
error. So every watcher remembers which object owns each connection and skips
the ones whose owner has been deleted.
"""

try:
    from qgis.PyQt import sip
except ImportError:  # pragma: no cover - plain PyQt / tests
    try:
        import sip  # type: ignore
    except ImportError:
        sip = None


def alive(obj) -> bool:
    """False when ``obj`` is None or its C++ object has been deleted."""
    if obj is None:
        return False
    if sip is None:
        return True
    try:
        return not sip.isdeleted(obj)
    except TypeError:          # not a wrapped Qt object
        return True


def safe_disconnect(owner, signal, slot) -> None:
    """Disconnect ``slot`` from ``signal`` unless ``owner`` has been deleted."""
    if signal is None or not alive(owner):
        return
    try:
        signal.disconnect(slot)
    except (TypeError, RuntimeError):
        pass


def disconnect_all(conns) -> None:
    """Disconnect a list of ``(owner, signal, slot)``."""
    for owner, signal, slot in conns:
        safe_disconnect(owner, signal, slot)
