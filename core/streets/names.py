# -*- coding: utf-8 -*-
"""
Street-name logic (pure Python, no QGIS).

* formatting a full road name from NENA name parts,
* canonical forms used for uniqueness checks and variant detection
  ("N Main St" == "North Main Street"),
* the configurable uniqueness rule (scope + strictness + sound-alike),
* grouping legacy spellings into variant groups for cleanup.
"""

import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Hashable, Iterable, List, Optional, Sequence, Tuple

from ..schema import DIRECTIONAL_ABBREVIATIONS, STREET_NAME_FIELDS

NAME_PARTS: Tuple[str, ...] = STREET_NAME_FIELDS   # St_PreMod … St_PosMod, in display order
TYPE_PARTS = ("St_PreTyp", "St_PosTyp")
DIR_PARTS = ("St_PreDir", "St_PosDir")

STRICT_FULL = "full"            # whole road name: directions + name + types (+ modifiers)
STRICT_IGNORE_TYPE = "ignore_type"  # "Oak St" == "Oak Ave"

STATUS_ACTIVE = "Active"
STATUS_PROPOSED = "Proposed"
STATUS_RETIRED = "Retired"
STATUSES = (STATUS_ACTIVE, STATUS_PROPOSED, STATUS_RETIRED)

# Common USPS street suffix / NENA street type abbreviations -> spelled-out form.
TYPE_ABBREVIATIONS = {
    "ALY": "ALLEY", "AVE": "AVENUE", "AV": "AVENUE", "AVN": "AVENUE", "BLVD": "BOULEVARD", "BR": "BRANCH",
    "BRG": "BRIDGE", "CIR": "CIRCLE", "CT": "COURT", "CV": "COVE", "CRK": "CREEK", "XING": "CROSSING",
    "DR": "DRIVE", "EXPY": "EXPRESSWAY", "EXT": "EXTENSION", "FWY": "FREEWAY", "GRV": "GROVE",
    "HTS": "HEIGHTS", "HL": "HILL", "HWY": "HIGHWAY", "HOLW": "HOLLOW", "JCT": "JUNCTION",
    "LNDG": "LANDING", "LN": "LANE", "MDW": "MEADOW", "ML": "MILL", "PKWY": "PARKWAY", "PL": "PLACE",
    "PLZ": "PLAZA", "PT": "POINT", "RDG": "RIDGE", "RD": "ROAD", "RTE": "ROUTE", "SPG": "SPRING",
    "SQ": "SQUARE", "ST": "STREET", "STR": "STREET", "TER": "TERRACE", "TRCE": "TRACE", "TRL": "TRAIL",
    "TPKE": "TURNPIKE", "VW": "VIEW", "VLG": "VILLAGE", "VIS": "VISTA", "WY": "WAY",
    # pre-types
    "CO RD": "COUNTY ROAD", "CR": "COUNTY ROAD", "CNTY RD": "COUNTY ROAD", "ST HWY": "STATE HIGHWAY",
    "SH": "STATE HIGHWAY", "US HWY": "US HIGHWAY", "SR": "STATE ROUTE", "ST RTE": "STATE ROUTE",
    "TWP RD": "TOWNSHIP ROAD", "FM": "FARM TO MARKET ROAD",
}
_DIRS = {k: v.upper() for k, v in DIRECTIONAL_ABBREVIATIONS.items()}


def clean(value) -> str:
    """Trim, collapse inner spaces; None -> ''."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def parts_from(mapping) -> Dict[str, str]:
    """Normalise any {part: value} mapping (missing parts -> '')."""
    return {p: clean(mapping.get(p)) for p in NAME_PARTS}


def full_name(parts) -> str:
    """'North Main Street' style full name in NENA part order."""
    return " ".join(clean(parts.get(p)) for p in NAME_PARTS if clean(parts.get(p)))


def canonical_part(part: str, value) -> str:
    v = clean(value).upper().replace(".", "")
    if not v:
        return ""
    v = " ".join(v.split())
    if part in DIR_PARTS:
        return _DIRS.get(v.replace(" ", ""), v)
    if part in TYPE_PARTS:
        return TYPE_ABBREVIATIONS.get(v, v)
    return v


def name_key(parts, strictness: str = STRICT_FULL) -> str:
    """Canonical comparison key of a road name under a strictness rule."""
    out = []
    for p in NAME_PARTS:
        if strictness == STRICT_IGNORE_TYPE and p in TYPE_PARTS:
            continue
        c = canonical_part(p, parts.get(p))
        if c:
            out.append(c)
    return " ".join(out)


def soundex(word: str) -> str:
    """American Soundex (4 characters)."""
    w = re.sub(r"[^A-Z]", "", (word or "").upper())
    if not w:
        return ""
    codes = {**dict.fromkeys("BFPV", "1"), **dict.fromkeys("CGJKQSXZ", "2"), **dict.fromkeys("DT", "3"),
             "L": "4", **dict.fromkeys("MN", "5"), "R": "6"}
    out, last = w[0], codes.get(w[0], "")
    for ch in w[1:]:
        code = codes.get(ch, "")
        if code and code != last:
            out += code
        if ch not in "HW":
            last = code
    return (out + "000")[:4]


def soundalike_key(parts) -> str:
    """Directions + sound-alike code of every word in the street name (types ignored)."""
    words = canonical_part("St_Name", parts.get("St_Name")).split()
    dirs = [canonical_part(p, parts.get(p)) for p in DIR_PARTS]
    return " ".join([dirs[0]] + [soundex(w) for w in words] + [dirs[1]]).strip()


def new_street_id() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


# ---------------------------------------------------------------------------
@dataclass
class StreetRecord:
    street_id: str
    parts: Dict[str, str]
    scope: str = ""
    status: str = STATUS_ACTIVE
    alias_of: str = ""
    fid: Optional[Hashable] = None
    extra: dict = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return full_name(self.parts)

    @property
    def active(self) -> bool:
        return self.status != STATUS_RETIRED


@dataclass
class UniquenessRule:
    scope_mode: str = "county"        # county | field
    strictness: str = STRICT_FULL
    soundalike: bool = True           # sound-alike names -> warning
    action: str = "block"             # block | warn

    def scope_of(self, scope_value) -> str:
        return "" if self.scope_mode == "county" else clean(scope_value).upper()


@dataclass
class Conflict:
    kind: str            # duplicate | soundalike
    other: StreetRecord
    blocking: bool


def find_conflicts(parts, scope_value, records: Iterable[StreetRecord], rule: UniquenessRule,
                   exclude_ids: Sequence[str] = ()) -> List[Conflict]:
    """Active names that clash with ``parts`` under the rule (aliases of excluded ids are skipped)."""
    key = name_key(parts, rule.strictness)
    sk = soundalike_key(parts) if rule.soundalike else None
    scope = rule.scope_of(scope_value)
    skip = set(exclude_ids)
    out = []
    for r in records:
        if not r.active or r.street_id in skip or (r.alias_of and r.alias_of in skip):
            continue
        if rule.scope_of(r.scope) != scope:
            continue
        if name_key(r.parts, rule.strictness) == key and key:
            out.append(Conflict("duplicate", r, rule.action == "block"))
        elif sk and soundalike_key(r.parts) == sk:
            out.append(Conflict("soundalike", r, False))
    return out


def duplicate_groups(records: Iterable[StreetRecord], rule: UniquenessRule) -> List[List[StreetRecord]]:
    """Groups of active master records that break the uniqueness rule."""
    groups = defaultdict(list)
    for r in records:
        if r.active and r.full_name:
            groups[(rule.scope_of(r.scope), name_key(r.parts, rule.strictness))].append(r)
    return [g for g in groups.values() if len(g) > 1]


# ---------------------------------------------------------------------------
@dataclass
class VariantGroup:
    key: str                                   # canonical full-name key (+ scope)
    scope: str
    spellings: Dict[Tuple[str, ...], int]      # raw parts tuple -> number of features
    chosen: Tuple[str, ...] = ()

    @property
    def is_variant(self) -> bool:
        return len(self.spellings) > 1

    def chosen_parts(self) -> Dict[str, str]:
        return dict(zip(NAME_PARTS, self.chosen))

    def label(self, spelling: Tuple[str, ...]) -> str:
        return full_name(dict(zip(NAME_PARTS, spelling)))


def _spelling_score(spelling: Tuple[str, ...], count: int):
    text = " ".join(s for s in spelling if s)
    mixed_case = text != text.upper() and text != text.lower()
    return (count, mixed_case, len(text))     # most used, then proper case, then the longer (spelled out)


def group_variants(named: Iterable[Tuple[Dict[str, str], str]]) -> List[VariantGroup]:
    """
    :param named: (parts, scope) for each feature.
    :returns: one group per canonical road name (and scope); ``chosen`` = the
              most common spelling (ties: proper case, then spelled out).
    """
    acc: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    for parts, scope in named:
        p = parts_from(parts)
        key = name_key(p, STRICT_FULL)
        if not key:
            continue
        acc[(scope or "", key)][tuple(p[n] for n in NAME_PARTS)] += 1
    groups = []
    for (scope, key), counter in sorted(acc.items()):
        chosen = max(counter.items(), key=lambda kv: _spelling_score(kv[0], kv[1]))[0]
        groups.append(VariantGroup(key=key, scope=scope, spellings=dict(counter), chosen=chosen))
    return groups


def soundalike_groups(groups: Sequence[VariantGroup]) -> List[List[VariantGroup]]:
    """Different names that sound alike (e.g. Smith Rd / Smyth Rd) – a warning list."""
    by = defaultdict(list)
    for g in groups:
        by[(g.scope, soundalike_key(g.chosen_parts()))].append(g)
    return [v for v in by.values() if len(v) > 1]
