# -*- coding: utf-8 -*-
"""Retire a road or address (status RET and Expire) and hide expired rows from the map."""

import copy
from datetime import date, datetime

from .addressing.engine import address_mapping
from .config import PluginConfig
from .layer_access import ConfigError, FieldAccessor, apply_changes
from .project_config import layer_for, resolve_layer
from .zones import ZoneLayer

RETIRED = "RET"


def _as_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    to_date = getattr(value, "toPyDate", None)
    if callable(to_date):
        try:
            return to_date()
        except Exception:  # noqa: BLE001
            return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def is_current(status, expire, today=None) -> bool:
    """False when the row is retired or its Expire date is today or earlier."""
    if str(status or "").strip().upper() in (RETIRED, "RETIRED"):
        return False
    day = _as_date(expire)
    if day is not None and day <= (today or date.today()):
        return False
    return True


def current_subset(provider: str, status_field: str = "") -> str:
    """Provider SQL that keeps rows with no Expire yet, and no RET status."""
    prov = (provider or "").lower()
    if prov == "postgres":
        expire = '"Expire" IS NULL OR "Expire" > CURRENT_TIMESTAMP'
        quote = '"{}"'
    elif prov == "mssql":
        expire = "[Expire] IS NULL OR [Expire] > GETDATE()"
        quote = "[{}]"
    else:
        expire = "\"Expire\" IS NULL OR \"Expire\" > date('now')"
        quote = '"{}"'
    if not status_field:
        return expire
    name = quote.format(status_field)
    return f"{expire} AND ({name} IS NULL OR {name} <> '{RETIRED}')"


def _accessor(layer, cfg, names):
    cfg = copy.copy(cfg) if cfg is not None else PluginConfig()
    cfg.mapping = dict(getattr(cfg, "mapping", None) or {})
    fields = layer.fields()
    for name in names:
        if name in cfg.mapping and cfg.mapping[name]:
            continue
        i = fields.lookupField(name)
        if i >= 0:
            cfg.mapping[name] = fields.at(i).name()
    return FieldAccessor(layer, cfg)


def retire_layer(layer, cfg, status_field: str, when=None) -> int:
    """Set RET and Expire on the selected features. Returns how many changed."""
    if layer is None:
        return 0
    ids = [fid for fid in (layer.selectedFeatureIds() or [])]
    if not ids:
        return 0
    acc = _accessor(layer, cfg, (status_field, "Expire"))
    if not acc.has(status_field) and not acc.has("Expire"):
        raise ConfigError(f"'{layer.name()}' has no {status_field} or Expire field. "
                          "Update schema, then retire.")
    stamp = when or datetime.now().replace(microsecond=0)
    changes = {}
    for fid in ids:
        vals = {}
        if acc.has(status_field):
            vals[status_field] = RETIRED
        if acc.has("Expire"):
            vals["Expire"] = stamp
        if vals:
            changes[fid] = vals
    return apply_changes(layer, acc, changes, "NENA: retire")


def retire_selected(centerline, rcl_cfg, pcfg) -> tuple:
    """Retire selected centerlines and address points. Returns (count, message)."""
    targets = []
    if centerline is not None:
        targets.append((centerline, rcl_cfg, "RoadStatus"))
    address = layer_for(pcfg.streets, "address") if pcfg is not None else None
    if address is not None:
        acfg = PluginConfig()
        acfg.mapping = address_mapping(address, pcfg)
        targets.append((address, acfg, "AddressStatus"))
    if not targets:
        return 0, "Choose the centerline and address layers first."
    selected = changed = 0
    for layer, cfg, field in targets:
        ids = list(layer.selectedFeatureIds() or [])
        if not ids:
            continue
        selected += len(ids)
        changed += retire_layer(layer, cfg, field)
    if not selected:
        return 0, "Select the roads or addresses to retire."
    if not changed:
        return 0, "The selection is already retired."
    return changed, (f"Retired {changed} feature(s). Status is RET and Expire is today. "
                     "The rows stay in the layer.")


def current_layers(centerline, pcfg):
    """(layer, status field) for the centerline, addresses, and configured zones."""
    out = []
    if centerline is not None:
        out.append((centerline, "RoadStatus"))
    if pcfg is not None:
        address = layer_for(pcfg.streets, "address")
        if address is not None:
            out.append((address, "AddressStatus"))
        for raw in pcfg.zones.layers or []:
            zone = ZoneLayer.from_dict(raw)
            lyr = resolve_layer(zone.layer_id, zone.layer_source)
            if lyr is not None:
                out.append((lyr, ""))
    return out


def _without(current: str, clause: str) -> str:
    text = current.replace(f"({clause})", " ").replace(clause, " ")
    text = " ".join(text.split())
    for junk in ("AND AND", "( AND", "AND )"):
        text = text.replace(junk, "AND")
    text = text.strip()
    for edge in ("AND ", " AND", "()"):
        if text.startswith(edge):
            text = text[len(edge):].strip()
        if text.endswith(edge.strip()):
            text = text[: -len(edge.strip())].strip()
    if text in ("AND", "()"):
        return ""
    return text


def set_current_view(layers, on: bool) -> int:
    """Show only current rows, or clear that filter. Returns how many layers changed."""
    if not layers:
        raise ConfigError("Choose the centerline and address layers first.")
    changed = 0
    skipped = []
    for layer, status_field in layers:
        if layer.fields().lookupField("Expire") < 0:
            skipped.append(layer.name())
            continue
        use_status = status_field if layer.fields().lookupField(status_field) >= 0 else ""
        clause = current_subset(layer.providerType(), use_status)
        current = (layer.subsetString() or "").strip()
        showing = clause in current
        if on and not showing:
            new = f"({current}) AND ({clause})" if current else clause
        elif not on and showing:
            new = _without(current, clause)
        else:
            continue
        if not layer.setSubsetString(new):
            raise ConfigError(f"Could not filter '{layer.name()}'.")
        changed += 1
    if not changed and on and skipped and not layers:
        raise ConfigError("No centerline or address layer to filter.")
    if on and not changed and skipped:
        raise ConfigError("Update schema so the layers have an Expire field, then show only current features.")
    return changed
