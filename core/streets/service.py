# -*- coding: utf-8 -*-
"""
Master street-name operations across layers.

* build the master list from the centerlines (variant groups, links)
* add / rename (cascade) / retire names with the uniqueness rule
* assign a master name to selected centerlines
* sync name parts from the master to every linked feature
* name checks for validation

All writes go through the layers' edit buffers as "NENA: …" commands (one
per layer) and are suppressed from the plugin's own watchers.
"""

import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Hashable, List, Optional, Sequence, Tuple

from qgis.core import QgsFeatureRequest, QgsVectorLayer

from ..config import PluginConfig
from ..layer_access import ConfigError, FieldAccessor, apply_changes, ensure_full_name_mapping
from ..project_config import ProjectConfig, layer_for
from ..rules import ERROR, INFO, WARNING, Issue
from ..schema import auto_map
from . import names as N
from .repository import StreetRepository
from .street_types import merged_abbreviations

_SUPPRESS = [0]


@contextlib.contextmanager
def suppressed():
    """Plugin-made edits: watchers ignore them."""
    _SUPPRESS[0] += 1
    try:
        yield
    finally:
        _SUPPRESS[0] -= 1


def is_suppressed() -> bool:
    return _SUPPRESS[0] > 0


class NameConflictError(ConfigError):
    def __init__(self, conflicts):
        self.conflicts = conflicts
        names = ", ".join(f"'{c.other.full_name}'" + (f" ({c.other.scope})" if c.other.scope else "")
                          for c in conflicts)
        super().__init__(f"The name breaks the uniqueness rule: already used by {names}.")


@dataclass
class BuildReport:
    groups: List[N.VariantGroup] = field(default_factory=list)
    created: int = 0
    reused: int = 0
    linked_centerlines: int = 0
    linked_addresses: int = 0
    rewritten: int = 0
    unnamed: int = 0
    soundalikes: List[List[N.VariantGroup]] = field(default_factory=list)


@dataclass
class LinkedLayer:
    """A layer whose features point at the master by StreetID (centerlines or address points)."""
    role: str
    layer: QgsVectorLayer
    acc: FieldAccessor

    @property
    def usable(self) -> bool:
        return self.acc.has("StreetID")


class StreetService:

    def __init__(self, pcfg: ProjectConfig, centerline_layer: Optional[QgsVectorLayer] = None,
                 centerline_cfg: Optional[PluginConfig] = None, master_layer: Optional[QgsVectorLayer] = None,
                 address_layer: Optional[QgsVectorLayer] = None):
        self.pcfg = pcfg
        s = pcfg.streets
        self.master = master_layer or layer_for(s, "master")
        self.repo = StreetRepository(self.master)
        self.rule = N.UniquenessRule(scope_mode=s.scope_mode, strictness=s.strictness,
                                     soundalike=s.soundalike, action=s.action)
        self.linked: List[LinkedLayer] = []
        rcl = centerline_layer or layer_for(s, "centerline")
        if rcl is not None:
            cfg = centerline_cfg or _cfg_from_layer(rcl)
            cfg = _with_street_id(cfg, rcl, s.street_id_field)
            self.linked.append(LinkedLayer("centerline", rcl, FieldAccessor(rcl, cfg)))
        addr = address_layer or layer_for(s, "address")
        if addr is not None:
            mapping = dict(s.address_mapping) or address_auto_mapping(addr, s.street_id_field)
            acfg = PluginConfig()
            acfg.mapping = mapping
            self.linked.append(LinkedLayer("address", addr, FieldAccessor(addr, acfg)))

    # -- helpers -------------------------------------------------------------
    def layer(self, role) -> Optional[LinkedLayer]:
        return next((ll for ll in self.linked if ll.role == role), None)

    def _stamp_vals(self, ll: LinkedLayer, vals: dict, stamp) -> dict:
        if self.pcfg.streets.stamp_date_update and ll.acc.has("DateUpdate"):
            vals["DateUpdate"] = stamp
        return vals

    def counts(self) -> Dict[str, Tuple[int, int]]:
        """StreetID -> (centerlines, addresses)."""
        out: Dict[str, List[int]] = {}
        for k, role in enumerate(("centerline", "address")):
            ll = self.layer(role)
            if ll is None or not ll.usable:
                continue
            idx = ll.acc.index["StreetID"]
            for f in ll.layer.getFeatures(QgsFeatureRequest().setSubsetOfAttributes([idx])):
                sid = N.clean(ll.acc.get(f, "StreetID"))
                if sid:
                    out.setdefault(sid, [0, 0])[k] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    def _abbr(self):
        extra = getattr(getattr(self.pcfg, "street_types", None), "abbreviations", None)
        return merged_abbreviations(extra)

    def conflicts(self, parts, scope="", exclude_ids: Sequence[str] = ()) -> List[N.Conflict]:
        return N.find_conflicts(parts, scope, self.repo.records(), self.rule, exclude_ids, self._abbr())

    def _check(self, parts, scope, exclude_ids=(), allow_warnings=True):
        if not N.clean(parts.get("St_Name")):
            raise ConfigError("A street name (St_Name) is required.")
        conf = self.conflicts(parts, scope, exclude_ids)
        blocking = [c for c in conf if c.blocking]
        if blocking:
            raise NameConflictError(blocking)
        return [c for c in conf if not c.blocking]

    # -- add / rename / retire ------------------------------------------------
    def add_street(self, parts, scope="", status=N.STATUS_ACTIVE) -> Tuple[N.StreetRecord, List[N.Conflict]]:
        warnings = self._check(parts, scope)
        rec = N.StreetRecord(N.new_street_id(), N.parts_from(parts), scope=scope, status=status)
        with suppressed():
            self.repo.add([rec])
        return rec, warnings

    def rename_preview(self, sid: str) -> Tuple[int, int]:
        return self.counts().get(sid, (0, 0))

    def rename(self, sid: str, new_parts, scope: Optional[str] = None,
               keep_alias: Optional[bool] = None) -> Dict[str, int]:
        recs = self.repo.by_id()
        rec = recs.get(sid)
        if rec is None:
            raise ConfigError("Street not found in the master table.")
        new_scope = rec.scope if scope is None else scope
        warnings = self._check(new_parts, new_scope, exclude_ids=[sid])
        keep = self.pcfg.streets.keep_alias_on_rename if keep_alias is None else keep_alias
        old = N.StreetRecord(N.new_street_id(), dict(rec.parts), scope=rec.scope, status=N.STATUS_RETIRED,
                             alias_of=sid)
        rec.parts = N.parts_from(new_parts)
        rec.scope = new_scope
        with suppressed():
            self.repo.update([rec], "NENA: rename street")
            abbr = self._abbr()
            if keep and N.name_key(old.parts, abbreviations=abbr) != N.name_key(rec.parts, abbreviations=abbr):
                self.repo.add([old], "NENA: keep old street name as alias")
            changed = self._push_parts({sid: rec}, "NENA: rename street (cascade)")
        changed["warnings"] = len(warnings)
        return changed

    def retire(self, sid: str) -> None:
        rec = self.repo.by_id().get(sid)
        if rec is None:
            raise ConfigError("Street not found.")
        rec.status = N.STATUS_RETIRED
        with suppressed():
            self.repo.update([rec], "NENA: retire street")

    # -- linking ---------------------------------------------------------------
    def _address_ready(self, ll: Optional[LinkedLayer]) -> Optional[LinkedLayer]:
        """Address points carry FullName. Add the column when this layer is the address layer."""
        if ll is None or ll.role != "address":
            return ll
        ll.acc = ensure_full_name_mapping(ll.layer, ll.acc)
        return ll

    def _name_vals(self, parts, street_id=None) -> dict:
        vals = dict(parts)
        if street_id is not None:
            vals["StreetID"] = street_id
        vals["FullName"] = N.full_name(parts) or None
        return vals

    def assign(self, sid: str, fids: Sequence[Hashable], role: str = "centerline") -> int:
        rec = self.repo.by_id().get(sid)
        if rec is None:
            raise ConfigError("Street not found.")
        if not N.clean(rec.parts.get("St_Name")):
            raise ConfigError("This master street has no street name.")
        ll = self._address_ready(self.layer(role))
        if ll is None or not ll.usable:
            raise ConfigError(f"The {role} layer has no '{self.pcfg.streets.street_id_field}' field. "
                              "Add it (Streets ▸ Add to layers) or map it.")
        stamp = datetime.now().replace(microsecond=0)
        changes = {fid: self._stamp_vals(ll, self._name_vals(rec.parts, sid), stamp) for fid in fids}
        with suppressed():
            return apply_changes(ll.layer, ll.acc, changes, "NENA: assign street name")

    def copy_parts(self, role: str, fids: Sequence[Hashable]) -> int:
        """Copy the master name parts onto features whose StreetID was just picked (e.g. in a form)."""
        ll = self._address_ready(self.layer(role))
        if ll is None or not ll.usable:
            return 0
        recs = self.repo.by_id()
        stamp = datetime.now().replace(microsecond=0)
        changes = {}
        for f in ll.layer.getFeatures(QgsFeatureRequest().setFilterFids(list(fids))):
            rec = recs.get(N.clean(ll.acc.get(f, "StreetID")))
            if rec is None or not N.clean(rec.parts.get("St_Name")):
                continue
            diff = {p: v or None for p, v in rec.parts.items() if ll.acc.has(p) and N.clean(ll.acc.get(f, p)) != v}
            full = N.full_name(rec.parts)
            if ll.acc.has("FullName") and N.clean(ll.acc.get(f, "FullName")) != full:
                diff["FullName"] = full or None
            if diff:
                changes[f.id()] = self._stamp_vals(ll, diff, stamp)
        with suppressed():
            return apply_changes(ll.layer, ll.acc, changes, "NENA: copy street name")

    def sync(self, sids: Optional[Sequence[str]] = None) -> Dict[str, int]:
        recs = self.repo.by_id()
        if sids is not None:
            recs = {k: v for k, v in recs.items() if k in sids}
        with suppressed():
            return self._push_parts(recs, "NENA: sync street names")

    def _push_parts(self, recs: Dict[str, N.StreetRecord], command: str) -> Dict[str, int]:
        """Copy master name parts onto every linked feature whose parts differ."""
        stamp = datetime.now().replace(microsecond=0)
        result = {}
        for ll in self.linked:
            ll = self._address_ready(ll)
            if not ll.usable:
                continue
            changes = {}
            for f in ll.layer.getFeatures():
                sid = N.clean(ll.acc.get(f, "StreetID"))
                rec = recs.get(sid)
                if rec is None or not N.clean(rec.parts.get("St_Name")):
                    continue
                diff = {p: v for p, v in rec.parts.items()
                        if ll.acc.has(p) and N.clean(ll.acc.get(f, p)) != v}
                full = N.full_name(rec.parts)
                if ll.acc.has("FullName") and N.clean(ll.acc.get(f, "FullName")) != full:
                    diff["FullName"] = full
                if diff:
                    changes[f.id()] = self._stamp_vals(ll, {p: (v or None) for p, v in diff.items()}, stamp)
            result[ll.role] = apply_changes(ll.layer, ll.acc, changes, command)
        return result

    # -- build from centerlines ------------------------------------------------
    def analyse_centerlines(self) -> BuildReport:
        """Variant groups of the names currently on the centerlines (nothing written)."""
        ll = self.layer("centerline")
        if ll is None:
            raise ConfigError("Choose the centerline layer first.")
        rep = BuildReport()
        named = []
        for f in ll.layer.getFeatures():
            parts = {p: ll.acc.get(f, p) for p in N.NAME_PARTS}
            # A direction or a street type alone is not a road name. Those used to
            # become master rows whose street name was blank.
            if not N.clean(parts.get("St_Name")):
                rep.unnamed += 1
                continue
            named.append((parts, self._scope_of(ll, f)))
        rep.groups = N.group_variants(named, self._abbr())
        rep.soundalikes = N.soundalike_groups(rep.groups)
        return rep

    def build_from_centerlines(self, rewrite_spellings: bool = True, link_addresses: bool = True,
                               overwrite_links: bool = False, chosen: Optional[dict] = None) -> BuildReport:
        """
        Create master records (one per canonical name and scope, reusing
        existing master records), write StreetID onto centerlines and
        address points with matching names, and optionally rewrite their
        name parts to the chosen spelling.

        :param chosen: optional {(scope, key): spelling tuple} overriding the
                       automatically chosen spelling of a variant group.
        """
        rep = self.analyse_centerlines()
        for g in rep.groups:
            if chosen and (g.scope, g.key) in chosen:
                g.chosen = chosen[(g.scope, g.key)]
        abbr = self._abbr()
        existing = {(self.rule.scope_of(r.scope), N.name_key(r.parts, abbreviations=abbr)): r
                    for r in self.repo.records() if r.active}
        to_add = []
        by_key: Dict[Tuple[str, str], N.StreetRecord] = {}
        for g in rep.groups:
            k = (self.rule.scope_of(g.scope), g.key)
            rec = existing.get(k) or by_key.get(k)
            if rec is None:
                rec = N.StreetRecord(N.new_street_id(), g.chosen_parts(), scope=g.scope)
                to_add.append(rec)
                rep.created += 1
            elif k in existing:
                rep.reused += 1
            by_key[k] = rec
        with suppressed():
            if to_add:
                self.repo.add(to_add, "NENA: build street names")
            for ll in self.linked:
                if ll.role == "address" and not link_addresses:
                    continue
                if not ll.usable:
                    continue
                n_link, n_rew = self._link_layer(ll, by_key, rewrite_spellings, overwrite_links)
                if ll.role == "centerline":
                    rep.linked_centerlines = n_link
                else:
                    rep.linked_addresses = n_link
                rep.rewritten += n_rew
        return rep

    def _scope_of(self, ll: LinkedLayer, f) -> str:
        s = self.pcfg.streets
        if s.scope_mode != "field" or not s.scope_field:
            return ""
        return N.clean(ll.acc.get(f, s.scope_field)).upper()

    def _link_layer(self, ll, by_key, rewrite, overwrite) -> Tuple[int, int]:
        ll = self._address_ready(ll)
        stamp = datetime.now().replace(microsecond=0)
        changes, linked, rewritten = {}, 0, 0
        recs = self.repo.by_id()   # read the master table once, not once per feature
        recs.update({r.street_id: r for r in by_key.values()})
        known = set(recs)
        abbr = self._abbr()
        for f in ll.layer.getFeatures():
            parts = {p: ll.acc.get(f, p) for p in N.NAME_PARTS}
            key = N.name_key(parts, abbreviations=abbr)
            if not key:
                continue
            current = N.clean(ll.acc.get(f, "StreetID"))
            if current and current in known and not overwrite:
                rec = recs.get(current)
                full = N.full_name(rec.parts) if rec is not None else ""
                if rec is not None and ll.acc.has("FullName") and N.clean(ll.acc.get(f, "FullName")) != full:
                    changes[f.id()] = self._stamp_vals(ll, {"FullName": full or None}, stamp)
                continue
            rec = by_key.get((self.rule.scope_of(self._scope_of(ll, f)), key))
            if rec is None:
                continue
            vals = {"StreetID": rec.street_id}
            if ll.acc.has("FullName"):
                vals["FullName"] = N.full_name(rec.parts) or None
            if rewrite:
                diff = {p: v or None for p, v in rec.parts.items()
                        if ll.acc.has(p) and N.clean(parts.get(p)) != v}
                if diff:
                    vals.update(diff)
                    rewritten += 1
            changes[f.id()] = self._stamp_vals(ll, vals, stamp)
            linked += 1
        apply_changes(ll.layer, ll.acc, changes, "NENA: link street names")
        return linked, rewritten

    # -- checks ------------------------------------------------------------------
    def name_issues(self, role: str = "centerline") -> List[Issue]:
        ll = self.layer(role)
        if ll is None:
            return []
        if not ll.usable:
            return [Issue(None, WARNING, "STREETID_NOT_MAPPED",
                          f"The {role} layer has no StreetID field – names cannot be checked against the master")]
        recs = self.repo.by_id()
        abbr = self._abbr()
        out = []
        for f in ll.layer.getFeatures():
            sid = N.clean(ll.acc.get(f, "StreetID"))
            parts = {p: N.clean(ll.acc.get(f, p)) for p in N.NAME_PARTS}
            named = bool(N.name_key(parts, abbreviations=abbr))
            if not sid:
                if named:
                    out.append(Issue(f.id(), WARNING, "STREETID_MISSING",
                                     f"'{N.full_name(parts)}' is not linked to a master street name"))
                continue
            rec = recs.get(sid)
            if rec is None:
                out.append(Issue(f.id(), ERROR, "STREETID_UNKNOWN", f"StreetID {sid} is not in the master table"))
                continue
            if rec.status == N.STATUS_RETIRED:
                out.append(Issue(f.id(), WARNING, "STREET_RETIRED", f"Uses retired name '{rec.full_name}'"))
            diff = [p for p in N.NAME_PARTS if ll.acc.has(p) and parts[p] != rec.parts.get(p, "")]
            if diff:
                out.append(Issue(f.id(), ERROR, "NAME_OUT_OF_SYNC",
                                 f"'{N.full_name(parts)}' differs from master '{rec.full_name}' "
                                 f"({', '.join(diff)}) – run Sync names"))
        return out

    def master_issues(self) -> List[Issue]:
        """Problems inside the master table (fid = master-table feature)."""
        out = []
        recs = self.repo.records()
        abbr = self._abbr()
        for g in N.duplicate_groups(recs, self.rule, abbr):
            names = " / ".join(r.full_name for r in g)
            for r in g:
                out.append(Issue(r.fid, ERROR if self.rule.action == "block" else WARNING, "NAME_DUPLICATE",
                                 f"Duplicate street name in {'the county' if not r.scope or self.rule.scope_mode == 'county' else r.scope}: {names}"))
        if self.rule.soundalike:
            groups = {}
            for r in recs:
                if r.active and r.full_name:
                    groups.setdefault((self.rule.scope_of(r.scope), N.soundalike_key(r.parts)), []).append(r)
            for g in groups.values():
                keys = {N.name_key(r.parts, self.rule.strictness, abbr) for r in g}
                if len(keys) > 1:
                    names = " / ".join(sorted(r.full_name for r in g))
                    for r in g:
                        out.append(Issue(r.fid, INFO, "NAME_SOUNDALIKE", f"Names sound alike: {names}"))
        ids = [r.street_id for r in recs]
        for r in recs:
            if not N.clean(r.parts.get("St_Name")):
                out.append(Issue(r.fid, ERROR, "STREET_NAME_EMPTY", "Street name is empty"))
            if not r.street_id:
                out.append(Issue(r.fid, ERROR, "STREETID_EMPTY", f"'{r.full_name}' has no StreetID"))
            elif ids.count(r.street_id) > 1:
                out.append(Issue(r.fid, ERROR, "STREETID_DUPLICATE", f"StreetID {r.street_id} is used twice"))
        return out


# ---------------------------------------------------------------------------
def _cfg_from_layer(layer) -> PluginConfig:
    from ..config import load_from_layer
    return load_from_layer(layer) or PluginConfig(mapping=auto_map([f.name() for f in layer.fields()]))


def _with_street_id(cfg: PluginConfig, layer, field_name: str) -> PluginConfig:
    if cfg.mapping.get("StreetID"):
        return cfg
    if layer.fields().lookupField(field_name) >= 0:
        cfg.mapping = dict(cfg.mapping, StreetID=layer.fields().at(layer.fields().lookupField(field_name)).name())
    return cfg


def address_auto_mapping(layer, street_id_field: str = "StreetID") -> Dict[str, str]:
    """NENA name parts, StreetID, FullName and DateUpdate on an address layer, matched by name / alias."""
    names = [f.name() for f in layer.fields()]
    m = {k: v for k, v in auto_map(names).items() if k in N.NAME_PARTS or k in ("DateUpdate", "StreetID")}
    i = layer.fields().lookupField(street_id_field)
    if i >= 0:
        m["StreetID"] = layer.fields().at(i).name()
    from ..layer_access import _FULL_NAME_ALIASES
    for alias in _FULL_NAME_ALIASES:
        j = layer.fields().lookupField(alias)
        if j >= 0:
            m["FullName"] = layer.fields().at(j).name()
            break
    return m


def add_link_field(layer, name: str = "StreetID") -> bool:
    """Add the StreetID link field (text, 38) to a layer through its edit buffer. False if it exists."""
    from ...compat import make_field
    from ..layer_access import ensure_editable
    if layer.fields().lookupField(name) >= 0:
        return False
    ensure_editable(layer)
    layer.beginEditCommand("NENA: add StreetID field")
    ok = layer.addAttribute(make_field(name, "string", 38))
    if not ok:
        layer.destroyEditCommand()
        raise ConfigError(f"The data provider refused to add '{name}' to {layer.name()}.")
    layer.endEditCommand()
    layer.updateFields()
    return True
