# -*- coding: utf-8 -*-
"""
Build the zip to upload to plugins.qgis.org.

    python scripts/make_zip.py

Writes dist/nena_rcl_tools-<version>.zip with a single top folder,
nena_rcl_tools/, as QGIS expects. Tests, screenshots, git files, caches
and this script are left out.
"""

import configparser
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = "nena_rcl_tools"
SKIP_DIRS = {".git", ".github", "__pycache__", ".pytest_cache", ".mypy_cache", "test", "dist", "scripts",
             "images"}
SKIP_EXT = {".pyc", ".pyo", ".zip", ".gpkg", ".qgz", ".qgs"}


def version():
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(os.path.join(ROOT, "metadata.txt"), encoding="utf-8")
    return cfg["general"]["version"]


def files():
    for here, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            if name.startswith(".") or os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            path = os.path.join(here, name)
            yield path, os.path.join(FOLDER, os.path.relpath(path, ROOT)).replace(os.sep, "/")


def main():
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{FOLDER}-{version()}.zip")
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, arc in files():
            info = zipfile.ZipInfo.from_file(path, arc)
            info.external_attr = 0o644 << 16          # no executable bits
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as fh:
                zf.writestr(info, fh.read())
            count += 1
    print(f"{out}  ({count} files, {os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main()
