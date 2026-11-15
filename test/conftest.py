# -*- coding: utf-8 -*-
"""
Test setup: when QGIS is not importable, the small fake in ``fake_qgis`` is put
on the path once for the whole session (the *_fake_qgis tests use it; with a
real QGIS they are skipped and the pure tests still run).
"""

import os
import sys

try:
    import qgis.core  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fake_qgis"))
    for _m in [m for m in sys.modules if m == "qgis" or m.startswith("qgis.")]:
        del sys.modules[_m]
