# -*- coding: utf-8 -*-
"""
NENA / USPS street types.

The spelled forms are the primary names from USPS Publication 28, plus the
pre-types agencies put in ``St_PreTyp`` (County Road, State Highway, and so
on). Abbreviations fold to those spelled forms for uniqueness. An agency can
add local types and local abbreviations in the project settings.
"""

from typing import Dict, Iterable, List, Optional

# Full spellings. The name form shows these; uniqueness compares the upper form.
_SPELLINGS = (
    "Alley", "Annex", "Arcade", "Avenue", "Bayou", "Beach", "Bend", "Bluff", "Bluffs", "Bottom",
    "Boulevard", "Branch", "Bridge", "Brook", "Brooks", "Burg", "Burgs", "Bypass", "Camp", "Canyon",
    "Cape", "Causeway", "Center", "Centers", "Circle", "Circles", "Cliff", "Cliffs", "Club", "Common",
    "Commons", "Corner", "Corners", "County Road", "Course", "Court", "Courts", "Cove", "Coves",
    "Creek", "Crescent", "Crest", "Crossing", "Crossroad", "Crossroads", "Curve", "Dale", "Dam",
    "Divide", "Drive", "Drives", "Estate", "Estates", "Expressway", "Extension", "Extensions",
    "Fall", "Falls", "Farm to Market Road", "Ferry", "Field", "Fields", "Fire Road", "Flat", "Flats",
    "Ford", "Fords", "Forest", "Forge", "Forges", "Fork", "Forks", "Fort", "Freeway", "Garden",
    "Gardens", "Gateway", "Glen", "Glens", "Green", "Greens", "Grove", "Groves", "Harbor", "Harbors",
    "Haven", "Heights", "Highway", "Hill", "Hills", "Hollow", "Inlet", "Interstate", "Island",
    "Islands", "Isle", "Junction", "Junctions", "Key", "Keys", "Knoll", "Knolls", "Lake", "Lakes",
    "Land", "Landing", "Lane", "Light", "Lights", "Loaf", "Lock", "Locks", "Lodge", "Loop", "Mall",
    "Manor", "Manors", "Meadow", "Meadows", "Mews", "Mill", "Mills", "Mission", "Motorway", "Mount",
    "Mountain", "Mountains", "Neck", "Orchard", "Oval", "Overpass", "Park", "Parks", "Parkway",
    "Parkways", "Pass", "Passage", "Path", "Pike", "Pine", "Pines", "Place", "Plain", "Plains",
    "Plaza", "Point", "Points", "Port", "Ports", "Prairie", "Private Road", "Radial", "Ramp", "Ranch",
    "Ranch Road", "Rapid", "Rapids", "Rest", "Ridge", "Ridges", "River", "Road", "Roads", "Route",
    "Row", "Rue", "Run", "Shoal", "Shoals", "Shore", "Shores", "Skyway", "Spring", "Springs", "Spur",
    "Spurs", "Square", "Squares", "State Highway", "State Route", "Station", "Stravenue", "Stream",
    "Street", "Streets", "Summit", "Terrace", "Throughway", "Township Road", "Trace", "Track",
    "Trafficway", "Trail", "Trailer", "Tunnel", "Turnpike", "US Highway", "Underpass", "Union",
    "Unions", "Valley", "Valleys", "Viaduct", "View", "Views", "Village", "Villages", "Ville",
    "Vista", "Walk", "Walks", "Wall", "Way", "Ways", "Well", "Wells",
)

OFFICIAL_TYPES: tuple = tuple(sorted(set(_SPELLINGS), key=lambda s: s.upper()))
OFFICIAL_BY_UPPER = {s.upper(): s for s in OFFICIAL_TYPES}

# Abbreviation -> spelled form in upper case. Existing keys stay as they are
# so "St" still matches "Street" and "CO RD" still matches "County Road".
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
    # further USPS primary suffixes
    "ANX": "ANNEX", "ARC": "ARCADE", "BCH": "BEACH", "BND": "BEND", "BLF": "BLUFF", "BLFS": "BLUFFS",
    "BTM": "BOTTOM", "BRK": "BROOK", "BRKS": "BROOKS", "BG": "BURG", "BGS": "BURGS", "BYP": "BYPASS",
    "CP": "CAMP", "CYN": "CANYON", "CPE": "CAPE", "CSWY": "CAUSEWAY", "CTR": "CENTER", "CTRS": "CENTERS",
    "CIRS": "CIRCLES", "CLF": "CLIFF", "CLFS": "CLIFFS", "CLB": "CLUB", "CMN": "COMMON", "CMNS": "COMMONS",
    "COR": "CORNER", "CORS": "CORNERS", "CRSE": "COURSE", "CTS": "COURTS", "CVS": "COVES", "CRES": "CRESCENT",
    "CRST": "CREST", "XRD": "CROSSROAD", "XRDS": "CROSSROADS", "CURV": "CURVE", "DL": "DALE", "DM": "DAM",
    "DV": "DIVIDE", "DRS": "DRIVES", "EST": "ESTATE", "ESTS": "ESTATES", "EXTS": "EXTENSIONS", "FLS": "FALLS",
    "FRY": "FERRY", "FLD": "FIELD", "FLDS": "FIELDS", "FLT": "FLAT", "FLTS": "FLATS", "FRD": "FORD",
    "FRDS": "FORDS", "FRST": "FOREST", "FRG": "FORGE", "FRGS": "FORGES", "FRK": "FORK", "FRKS": "FORKS",
    "FT": "FORT", "GDN": "GARDEN", "GDNS": "GARDENS", "GTWY": "GATEWAY", "GLN": "GLEN", "GLNS": "GLENS",
    "GRN": "GREEN", "GRNS": "GREENS", "GRVS": "GROVES", "HBR": "HARBOR", "HBRS": "HARBORS", "HVN": "HAVEN",
    "HLS": "HILLS", "INLT": "INLET", "IS": "ISLAND", "ISS": "ISLANDS", "JCTS": "JUNCTIONS", "KY": "KEY",
    "KYS": "KEYS", "KNL": "KNOLL", "KNLS": "KNOLLS", "LK": "LAKE", "LKS": "LAKES", "LGT": "LIGHT",
    "LGTS": "LIGHTS", "LF": "LOAF", "LCK": "LOCK", "LCKS": "LOCKS", "LDG": "LODGE", "MNR": "MANOR",
    "MNRS": "MANORS", "MDWS": "MEADOWS", "MLS": "MILLS", "MSN": "MISSION", "MTWY": "MOTORWAY", "MT": "MOUNT",
    "MTN": "MOUNTAIN", "MTNS": "MOUNTAINS", "NCK": "NECK", "ORCH": "ORCHARD", "OPAS": "OVERPASS",
    "PKWYS": "PARKWAYS", "PSGE": "PASSAGE", "PNE": "PINE", "PNES": "PINES",
    "PLN": "PLAIN", "PLNS": "PLAINS", "PTS": "POINTS", "PRT": "PORT", "PRTS": "PORTS", "PR": "PRAIRIE",
    "RADL": "RADIAL", "RNCH": "RANCH", "RPDS": "RAPIDS", "RST": "REST", "RDGS": "RIDGES", "RIV": "RIVER",
    "RDS": "ROADS", "SHL": "SHOAL", "SHLS": "SHOALS", "SHR": "SHORE", "SHRS": "SHORES", "SKWY": "SKYWAY",
    "SPGS": "SPRINGS", "SQS": "SQUARES", "STA": "STATION", "STRA": "STRAVENUE", "STRM": "STREAM",
    "STS": "STREETS", "SMT": "SUMMIT", "TRWY": "THROUGHWAY", "TRAK": "TRACK", "TRFY": "TRAFFICWAY",
    "TRLR": "TRAILER", "TUNL": "TUNNEL", "UPAS": "UNDERPASS", "UN": "UNION", "UNS": "UNIONS",
    "VLY": "VALLEY", "VLYS": "VALLEYS", "VIA": "VIADUCT", "VWS": "VIEWS", "VLGS": "VILLAGES", "VL": "VILLE",
    "IH": "INTERSTATE", "PVT RD": "PRIVATE ROAD", "PVT": "PRIVATE ROAD", "RR": "RANCH ROAD",
    "FIRE RD": "FIRE ROAD",
}

_KEEP_UPPER = {"US"}


def _key(value) -> str:
    return " ".join(str(value or "").upper().replace(".", "").split())


def _title_words(upper: str) -> str:
    out = []
    for word in upper.split():
        out.append(word if word in _KEEP_UPPER else word.capitalize())
    return " ".join(out)


def merged_abbreviations(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Built-in abbreviations, then any agency abbreviations (those win)."""
    out = dict(TYPE_ABBREVIATIONS)
    for key, value in (extra or {}).items():
        k, v = _key(key), _key(value)
        if k and v:
            out[k] = v
    return out


def canonical_type(value, abbreviations: Optional[Dict[str, str]] = None) -> str:
    """Upper spelled form used by uniqueness. Unknown text is returned uppercased."""
    key = _key(value)
    if not key:
        return ""
    table = TYPE_ABBREVIATIONS if abbreviations is None else abbreviations
    return table.get(key, key)


def display_type(value, abbreviations: Optional[Dict[str, str]] = None) -> str:
    """
    Official spelling for a type the user typed (``ST`` -> ``Street``).

    A type that is not in the list is returned as typed, so a one-off local
    name is not rewritten.
    """
    raw = " ".join(str(value or "").split())
    if not raw:
        return ""
    key = _key(raw)
    table = TYPE_ABBREVIATIONS if abbreviations is None else abbreviations
    spelled = table.get(key, key)
    official = OFFICIAL_BY_UPPER.get(spelled)
    if official:
        return official
    if key in table:
        return _title_words(table[key])
    return raw


def type_list(settings=None) -> List[str]:
    """Types for the name form. An unedited project uses the built-in list."""
    if settings is not None and getattr(settings, "custom", False):
        seen = set()
        out = []
        for item in settings.types or []:
            text = " ".join(str(item).split())
            mark = text.upper()
            if text and mark not in seen:
                seen.add(mark)
                out.append(text)
        return out
    return list(OFFICIAL_TYPES)


def match_types(text: str, types: Iterable[str]) -> List[str]:
    """Case-insensitive contains match, for the name-form completer."""
    needle = " ".join((text or "").split()).upper()
    if not needle:
        return list(types)
    return [item for item in types if needle in item.upper()]
