# -*- coding: utf-8 -*-
"""
Zone layers: PSAP, provisioning, law, fire, EMS, and other boundary polygons.

Plain Python. The NG9-1-1 call-routing service finds the agency for a
location by testing which polygon it falls in, so zones are polygon layers of
their own (NENA-STA-006 emergency service, PSAP, and provisioning boundaries,
plus city limits, communities, county, and ESN zones). Nothing is copied from
them onto roads or addresses; ZIP, MSAG, ESN, and city values for the road
sides stay under Settings, Sides.

Each configured zone layer has:

* a kind (fire, law, EMS, PSAP, provisioning, …) that decides its NENA fields
  and expected ServiceURN,
* the field whose value names the zone (DsplayName by default),
* **Split roads at this boundary**, on by default,
* which checks run against it.

Settings live in the project (``ProjectConfig.zones``).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .schema import MANDATORY, OPTIONAL, FieldDef


@dataclass(frozen=True)
class ZoneKind:
    key: str
    title: str
    table: str                      # table name when Create schema makes it
    urn_tokens: tuple = ()          # ServiceURN must end with one of these (after the prefix)
    covers_area: bool = True        # should tile the provisioning area with no gaps
    nguid_field: str = "ES_NGUID"
    nguid_kind: str = "ESB"


ZONE_KINDS = (
    ZoneKind("psap", "PSAP boundary", "PsapBoundary", ("sos", "sos.psap")),
    ZoneKind("provisioning", "Provisioning boundary", "ProvisioningBoundary", (), True, "PB_NGUID", "PB"),
    ZoneKind("law", "Law (police / sheriff)", "LawBoundary", ("responder.police",)),
    ZoneKind("fire", "Fire", "FireBoundary", ("responder.fire",)),
    ZoneKind("ems", "EMS", "EmsBoundary", ("responder.ems",)),
    ZoneKind("other_esb", "Other emergency service", "OtherServiceBoundary", ("responder.",), False),
    ZoneKind("incmuni", "Incorporated municipality", "IncorporatedMunicipality", (), False, "IncM_NGUID", "INCM"),
    ZoneKind("uninc", "Unincorporated community", "UnincorporatedCommunity", (), False, "UnincNGUID", "UNIN"),
    ZoneKind("nbrhd", "Neighborhood community", "NeighborhoodCommunity", (), False, "NbrhdNGUID", "NBRH"),
    ZoneKind("county", "County", "CountyBoundary", (), True, "CntyNGUID", "CNTY"),
    ZoneKind("esz", "ESZ / ESN", "EmergencyServiceZone", (), True, "ESZ_NGUID", "ESZ"),
)
KINDS = {k.key: k for k in ZONE_KINDS}
ESB_KINDS = ("psap", "law", "fire", "ems", "other_esb")

# One switch per check. A zone layer runs the checks that are on.
CHECKS = (
    ("geometry", "Invalid or empty polygons"),
    ("overlaps", "Polygons of this layer overlap"),
    ("gaps", "Gaps between polygons (inside the provisioning boundary)"),
    ("outside", "Polygons outside the provisioning boundary"),
    ("fields", "Required fields, unique NGUIDs, ServiceURN"),
    ("roads", "Roads that cross this boundary but are not split there"),
    ("addresses", "Addresses whose road side or access point is in another zone"),
)
CHECK_KEYS = tuple(key for key, _title in CHECKS)


def _esb_fields(nguid: str = "ES_NGUID") -> List[FieldDef]:
    S, DT = "string", "datetime"
    return [
        FieldDef("DiscrpAgID", MANDATORY, S, 75, "Discrepancy agency ID"),
        FieldDef("DateUpdate", MANDATORY, DT, 0, "Date updated"),
        FieldDef("Effective", OPTIONAL, DT, 0, "Effective date"),
        FieldDef("Expire", OPTIONAL, DT, 0, "Expiration date"),
        FieldDef(nguid, MANDATORY, S, 254, "NENA globally unique ID", (nguid.upper(),)),
        FieldDef("State", MANDATORY, S, 2, "State"),
        FieldDef("Agency_ID", MANDATORY, S, 100, "Agency ID", ("AGENCYID", "AGENCY")),
        FieldDef("ServiceURI", MANDATORY, S, 254, "Service URI", ("SERVICE_URI", "URI")),
        FieldDef("ServiceURN", MANDATORY, S, 50, "Service URN", ("SERVICE_URN", "URN")),
        FieldDef("ServiceNum", OPTIONAL, S, 15, "Service number"),
        FieldDef("AVcard_URI", MANDATORY, S, 254, "Agency vCard URI", ("AVCARDURI", "VCARD_URI")),
        FieldDef("DsplayName", MANDATORY, S, 60, "Display name", ("DISPLAYNAME", "DISPLAY_NAME", "NAME")),
    ]


def _area_fields(nguid: str, value: str, value_desc: str) -> List[FieldDef]:
    S, DT = "string", "datetime"
    return [
        FieldDef("DiscrpAgID", MANDATORY, S, 75, "Discrepancy agency ID"),
        FieldDef("DateUpdate", MANDATORY, DT, 0, "Date updated"),
        FieldDef("Effective", OPTIONAL, DT, 0, "Effective date"),
        FieldDef("Expire", OPTIONAL, DT, 0, "Expiration date"),
        FieldDef(nguid, MANDATORY, S, 254, "NENA globally unique ID", (nguid.upper(),)),
        FieldDef("Country", MANDATORY, S, 2, "Country"),
        FieldDef("State", MANDATORY, S, 2, "State"),
        FieldDef("County", MANDATORY, S, 100, "County"),
        FieldDef(value, MANDATORY, S, 100, value_desc, ("NAME",)),
    ]


_AREA_VALUE = {
    "incmuni": ("Inc_Muni", "Incorporated municipality"),
    "uninc": ("Uninc_Comm", "Unincorporated community"),
    "nbrhd": ("Nbrhd_Comm", "Neighborhood community"),
    "county": ("County", "County"),
}


def zone_field_defs(kind_key: str) -> List[FieldDef]:
    """NENA fields of a zone layer created by Create schema."""
    kind = KINDS[kind_key]
    if kind_key == "provisioning":
        return [d for d in _esb_fields("PB_NGUID")
                if d.name in ("DiscrpAgID", "DateUpdate", "Effective", "Expire", "PB_NGUID")]
    if kind_key == "esz":
        S = "string"
        return _esb_fields("ESZ_NGUID")[:6] + [
            FieldDef("ESN", MANDATORY, S, 5, "Emergency service number"),
            FieldDef("DsplayName", OPTIONAL, S, 60, "Display name", ("DISPLAYNAME", "NAME")),
        ]
    if kind_key in _AREA_VALUE:
        value, desc = _AREA_VALUE[kind_key]
        defs = _area_fields(kind.nguid_field, value, desc)
        if kind_key == "county":   # County is the value itself
            seen, out = set(), []
            for d in defs:
                if d.name not in seen:
                    seen.add(d.name)
                    out.append(d)
            return out
        return defs
    return _esb_fields(kind.nguid_field)


def default_value_field(kind_key: str) -> str:
    if kind_key == "provisioning":
        return "DiscrpAgID"
    if kind_key == "esz":
        return "ESN"
    if kind_key in _AREA_VALUE:
        return _AREA_VALUE[kind_key][0]
    return "DsplayName"


def required_fields(kind_key: str) -> List[str]:
    return [d.name for d in zone_field_defs(kind_key) if d.req == MANDATORY]


def guess_kind(layer_name: str) -> str:
    """Zone kind from a layer name, or ''."""
    text = (layer_name or "").lower().replace("_", " ").replace("-", " ")
    words = (
        ("provisioning", ("provision",)),
        ("psap", ("psap",)),
        ("esz", ("esz", "esn", "emergency service zone")),
        ("ems", ("ems", "ambulance", "medical")),
        ("fire", ("fire",)),
        ("law", ("law", "police", "sheriff")),
        ("incmuni", ("city limit", "incorporated", "municipal", "inc muni", "incmuni")),
        ("uninc", ("unincorporated", "uninc")),
        ("nbrhd", ("neighborhood", "nbrhd", "subdivision")),
        ("county", ("county",)),
    )
    for key, tokens in words:
        if any(tok in text for tok in tokens):
            if key == "incmuni" and "unincorporated" in text:
                continue
            return key
    return ""


@dataclass
class ZoneLayer:
    """One configured zone layer."""

    layer_id: str = ""
    layer_source: str = ""
    kind: str = "fire"
    value_field: str = ""
    split_roads: bool = True
    checks: Dict[str, bool] = field(default_factory=dict)
    gap_tolerance_m2: float = 1.0       # gaps and overlaps smaller than this are ignored

    def check_on(self, key: str) -> bool:
        if key in self.checks:
            return bool(self.checks[key])
        return default_check(self.kind, key, self.split_roads)

    @property
    def title(self) -> str:
        kind = KINDS.get(self.kind)
        return kind.title if kind else self.kind

    def to_dict(self) -> dict:
        return {"layer_id": self.layer_id, "layer_source": self.layer_source, "kind": self.kind,
                "value_field": self.value_field, "split_roads": bool(self.split_roads),
                "checks": {k: bool(v) for k, v in self.checks.items()},
                "gap_tolerance_m2": float(self.gap_tolerance_m2)}

    @classmethod
    def from_dict(cls, data: dict) -> "ZoneLayer":
        data = data or {}
        kind = str(data.get("kind") or "fire")
        if kind not in KINDS:
            kind = "other_esb"
        checks = data.get("checks") or {}
        try:
            tol = float(data.get("gap_tolerance_m2", 1.0))
        except (TypeError, ValueError):
            tol = 1.0
        return cls(
            layer_id=str(data.get("layer_id") or ""),
            layer_source=str(data.get("layer_source") or ""),
            kind=kind,
            value_field=str(data.get("value_field") or default_value_field(kind)),
            split_roads=bool(data.get("split_roads", True)),
            checks={str(k): bool(v) for k, v in checks.items() if str(k) in CHECK_KEYS} if isinstance(checks, dict)
            else {},
            gap_tolerance_m2=max(0.0, tol),
        )


def default_check(kind_key: str, check: str, split_roads: bool = True) -> bool:
    """Which checks start on for a new zone layer."""
    kind = KINDS.get(kind_key)
    if check == "roads":
        return bool(split_roads)
    if check == "gaps":
        return bool(kind and kind.covers_area and kind_key != "provisioning")
    if check == "outside":
        return kind_key != "provisioning"
    if check == "addresses":
        return kind_key != "provisioning"
    return True


def new_zone(layer_id: str, layer_source: str, kind_key: str, value_field: str = "") -> ZoneLayer:
    """A zone layer as it is first added: split roads on, the kind's usual checks on."""
    if kind_key not in KINDS:
        kind_key = "other_esb"
    return ZoneLayer(layer_id, layer_source, kind_key, value_field or default_value_field(kind_key), True, {})


def load_zones(raw: Optional[Sequence[dict]]) -> List[ZoneLayer]:
    return [ZoneLayer.from_dict(item) for item in (raw or []) if isinstance(item, dict)]


def dump_zones(zones: Sequence[ZoneLayer]) -> List[dict]:
    return [zone.to_dict() for zone in zones]


# ---------------------------------------------------------------------------
# Field checks (plain values)
# ---------------------------------------------------------------------------
_URN_PREFIXES = ("urn:emergency:service:", "urn:nena:service:", "urn:service:", "nena:service:")


def urn_matches(kind_key: str, urn) -> bool:
    """True when ``urn`` is a ServiceURN this kind of zone may carry. Old and new URN forms both pass."""
    kind = KINDS.get(kind_key)
    if kind is None or not kind.urn_tokens:
        return True
    text = str(urn or "").strip().lower()
    if not text:
        return False
    rest = text
    for prefix in _URN_PREFIXES:
        if text.startswith(prefix):
            rest = text[len(prefix):]
            break
    else:
        return False
    for token in kind.urn_tokens:
        token = token.lower()
        if token.endswith("."):
            if rest.startswith(token):
                return True
        elif rest == token or rest.startswith(token + "."):
            return True
    return False


def blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def esz_conflicts(members: Dict[str, Dict[str, set]]) -> List[tuple]:
    """
    ESZ polygons that fall in more than one zone of a service.

    ``members`` maps an ESZ id to ``{service kind: {zone values it overlaps}}``.
    Returns ``(esz id, kind, sorted values)`` for each service with more than one.
    """
    out = []
    for esz, by_kind in members.items():
        for kind, values in sorted(by_kind.items()):
            vals = sorted(v for v in values if not blank(v))
            if len(vals) > 1:
                out.append((esz, kind, vals))
    return out


def side_rules(pcfg) -> List[dict]:
    """
    The Settings, Sides rules, plus PSAP from the PSAP zone layer.

    PSAP_L / PSAP_R come from a Sides rule for PSAP when there is one.
    Otherwise the PSAP zone layer (Settings, Zones) and its name field are
    used, so the PSAP boundary only has to be set up once.
    """
    rules = [dict(r) for r in (pcfg.addresses.attribution_rules or []) if isinstance(r, dict)]
    if any(r.get("target") == "PSAP" for r in rules):
        return rules
    for zone in load_zones(pcfg.zones.layers):
        if zone.kind == "psap" and zone.layer_id and zone.value_field:
            rules.append({"layer_id": zone.layer_id, "layer_source": zone.layer_source,
                          "source_field": zone.value_field, "target": "PSAP", "optional": True})
            break
    return rules
