# -*- coding: utf-8 -*-
"""
NENA NG9-1-1 Road Centerline (RCL) schema definitions.

Based on NENA-STA-006.2 (NG9-1-1 GIS Data Model), Road Centerline layer.
The M / C / O (Mandatory / Conditional / Optional) flags reflect the
standard as commonly implemented; always verify against the edition your
9-1-1 authority has adopted. Everything here is plain Python (no QGIS
imports) so it can be unit-tested and reused.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import re

MANDATORY = "M"
CONDITIONAL = "C"
OPTIONAL = "O"

# Logical field types used when the plugin creates missing fields.
T_STRING = "string"
T_INT = "int"
T_DATETIME = "datetime"
T_DOUBLE = "double"
FIELD_TYPES = (T_STRING, T_INT, T_DATETIME, T_DOUBLE)
EDITOR_WIDTH = 100


@dataclass(frozen=True)
class FieldDef:
    name: str                 # NENA field name (the "logical" key used everywhere in the plugin)
    req: str                  # M / C / O
    ftype: str                # string / int / datetime
    length: int = 0           # suggested width for string fields
    description: str = ""
    aliases: tuple = field(default_factory=tuple)  # common legacy names used for auto-mapping
    create_as: str = ""       # physical name to use when creating the field (default: name); set by templates

    @property
    def physical_name(self) -> str:
        return self.create_as or self.name


# Local editor-tracking columns. Not part of NENA-STA-006. On a database layer the
# plugin fills them from the QGIS connection login (see core/editor_user.py).
WHO_ENTERED = FieldDef(
    "WhoEntered", OPTIONAL, T_STRING, EDITOR_WIDTH,
    "User who first saved the record. A database layer uses the connection login when this is empty.",
    ("WHO_ENTERED", "ENTEREDBY", "ENTERED_BY", "CREATEDBY", "CREATED_BY", "CREATED_USER",
     "CREATEUSER", "ADDUSER", "ADD_USER", "INSERTEDBY", "INSERTED_BY"))
LAST_UPDATED_BY = FieldDef(
    "LastUpdatedBy", OPTIONAL, T_STRING, EDITOR_WIDTH,
    "User who last saved the record. A database layer uses the connection login.",
    ("LAST_UPDATED_BY", "LASTUPDATEDBY", "UPDATEDBY", "UPDATED_BY", "LASTEDITEDBY",
     "LAST_EDITED_BY", "LAST_EDITED_USER", "EDITEDBY", "EDITED_BY", "MODIFIEDBY",
     "MODIFIED_BY", "UPDATEUSER", "UPDATE_USER", "LASTUPDATEBY", "LAST_UPDATE_BY"))


RCL_FIELDS: List[FieldDef] = [
    FieldDef("DiscrpAgID", MANDATORY, T_STRING, 100, "Discrepancy Agency ID (domain of the agency that resolves discrepancies)",
             ("DISCRPAGID", "DISCREPANCY_AGENCY")),
    FieldDef("DateUpdate", MANDATORY, T_DATETIME, 0, "Date the record was last updated",
             ("DATE_UPD", "LASTUPDATE", "LAST_EDITED_DATE", "EDITDATE", "UPDATED")),
    WHO_ENTERED,
    LAST_UPDATED_BY,
    FieldDef("Effective", OPTIONAL, T_DATETIME, 0, "Date the record becomes effective", ("EFF_DATE",)),
    FieldDef("Expire", OPTIONAL, T_DATETIME, 0, "Date the record expires", ("EXP_DATE",)),
    FieldDef("RCL_NGUID", MANDATORY, T_STRING, 254, "Road Centerline NENA Globally Unique ID",
             ("NGUID", "RCLNGUID", "RCL_NGUID", "SEGMENT_ID")),
    FieldDef("AdNumPre_L", OPTIONAL, T_STRING, 15, "Left address number prefix", ("ADNUMPREL",)),
    FieldDef("AdNumPre_R", OPTIONAL, T_STRING, 15, "Right address number prefix", ("ADNUMPRER",)),
    FieldDef("FromAddr_L", MANDATORY, T_INT, 0, "Left FROM address (at the start vertex)",
             ("L_F_ADD", "LFROMADD", "L_ADD_FROM", "FROMLEFT", "LEFTFROM", "L_FROM", "FRADDL", "LFROMADDR", "FROM_LEFT")),
    FieldDef("ToAddr_L", MANDATORY, T_INT, 0, "Left TO address (at the end vertex)",
             ("L_T_ADD", "LTOADD", "L_ADD_TO", "TOLEFT", "LEFTTO", "L_TO", "TOADDL", "LTOADDR", "TO_LEFT")),
    FieldDef("FromAddr_R", MANDATORY, T_INT, 0, "Right FROM address (at the start vertex)",
             ("R_F_ADD", "RFROMADD", "R_ADD_FROM", "FROMRIGHT", "RIGHTFROM", "R_FROM", "FRADDR", "RFROMADDR", "FROM_RIGHT")),
    FieldDef("ToAddr_R", MANDATORY, T_INT, 0, "Right TO address (at the end vertex)",
             ("R_T_ADD", "RTOADD", "R_ADD_TO", "TORIGHT", "RIGHTTO", "R_TO", "TOADDR", "RTOADDR", "TO_RIGHT")),
    FieldDef("Parity_L", MANDATORY, T_STRING, 1, "Left parity: O(dd) E(ven) B(oth) Z(ero)",
             ("PARITYL", "L_PARITY", "PARITY_LEFT")),
    FieldDef("Parity_R", MANDATORY, T_STRING, 1, "Right parity: O(dd) E(ven) B(oth) Z(ero)",
             ("PARITYR", "R_PARITY", "PARITY_RIGHT")),
    FieldDef("St_PreMod", OPTIONAL, T_STRING, 15, "Street name pre modifier", ("PREMOD",)),
    FieldDef("St_PreDir", CONDITIONAL, T_STRING, 9, "Street name pre directional (spelled out)",
             ("PREDIR", "PRE_DIR", "PREFIX", "DIRPRE", "PREFIXDIR", "FEDIRP")),
    FieldDef("St_PreTyp", CONDITIONAL, T_STRING, 35, "Street name pre type", ("PRETYPE", "PRE_TYPE", "PRETYP", "FETYPEP")),
    FieldDef("St_PreSep", CONDITIONAL, T_STRING, 20, "Street name pre type separator", ("PRESEP",)),
    FieldDef("St_Name", CONDITIONAL, T_STRING, 60, "Street name",
             ("STREETNAME", "STNAME", "STR_NAME", "STREET", "NAME", "FENAME", "ST_NAME", "ROADNAME", "ROAD_NAME")),
    FieldDef("St_PosTyp", CONDITIONAL, T_STRING, 35, "Street name post type (suffix)",
             ("SUFFIX", "STTYPE", "ST_TYPE", "POSTTYPE", "POST_TYPE", "STREETTYPE", "FETYPE", "TYPE", "SUFTYPE")),
    FieldDef("St_PosDir", CONDITIONAL, T_STRING, 9, "Street name post directional (spelled out)",
             ("POSTDIR", "POST_DIR", "SUFDIR", "SUF_DIR", "SUFFIXDIR", "FEDIRS")),
    FieldDef("St_PosMod", OPTIONAL, T_STRING, 25, "Street name post modifier", ("POSTMOD",)),
    FieldDef("ESN_L", CONDITIONAL, T_STRING, 5, "Left Emergency Service Number", ("ESNL", "L_ESN")),
    FieldDef("ESN_R", CONDITIONAL, T_STRING, 5, "Right Emergency Service Number", ("ESNR", "R_ESN")),
    FieldDef("MSAGComm_L", CONDITIONAL, T_STRING, 30, "Left MSAG community name", ("MSAG_L", "L_MSAG", "MSAGCOMML", "MSAGL")),
    FieldDef("MSAGComm_R", CONDITIONAL, T_STRING, 30, "Right MSAG community name", ("MSAG_R", "R_MSAG", "MSAGCOMMR", "MSAGR")),
    FieldDef("Country_L", MANDATORY, T_STRING, 2, "Left country (ISO 3166-1 alpha-2, e.g. US)", ("COUNTRYL",)),
    FieldDef("Country_R", MANDATORY, T_STRING, 2, "Right country (ISO 3166-1 alpha-2, e.g. US)", ("COUNTRYR",)),
    FieldDef("State_L", MANDATORY, T_STRING, 2, "Left state (2-letter USPS)", ("STATEL", "L_STATE")),
    FieldDef("State_R", MANDATORY, T_STRING, 2, "Right state (2-letter USPS)", ("STATER", "R_STATE")),
    FieldDef("County_L", MANDATORY, T_STRING, 100, "Left county (full name)", ("COUNTYL", "L_COUNTY")),
    FieldDef("County_R", MANDATORY, T_STRING, 100, "Right county (full name)", ("COUNTYR", "R_COUNTY")),
    FieldDef("AddCode_L", CONDITIONAL, T_STRING, 6, "Left additional code", ("ADDCODEL",)),
    FieldDef("AddCode_R", CONDITIONAL, T_STRING, 6, "Right additional code", ("ADDCODER",)),
    FieldDef("IncMuni_L", MANDATORY, T_STRING, 100, "Left incorporated municipality", ("INCMUNIL", "L_MUNI", "MUNI_L", "CITY_L")),
    FieldDef("IncMuni_R", MANDATORY, T_STRING, 100, "Right incorporated municipality", ("INCMUNIR", "R_MUNI", "MUNI_R", "CITY_R")),
    FieldDef("UnincCom_L", OPTIONAL, T_STRING, 100, "Left unincorporated community", ("UNINCCOML",)),
    FieldDef("UnincCom_R", OPTIONAL, T_STRING, 100, "Right unincorporated community", ("UNINCCOMR",)),
    FieldDef("NbrhdCom_L", OPTIONAL, T_STRING, 100, "Left neighborhood community", ("NBRHDCOML",)),
    FieldDef("NbrhdCom_R", OPTIONAL, T_STRING, 100, "Right neighborhood community", ("NBRHDCOMR",)),
    FieldDef("PostCode_L", CONDITIONAL, T_STRING, 7, "Left postal (ZIP) code", ("ZIP_L", "L_ZIP", "ZIPL", "ZIPLEFT", "POSTCODEL")),
    FieldDef("PostCode_R", CONDITIONAL, T_STRING, 7, "Right postal (ZIP) code", ("ZIP_R", "R_ZIP", "ZIPR", "ZIPRIGHT", "POSTCODER")),
    FieldDef("PostComm_L", CONDITIONAL, T_STRING, 40, "Left postal community name", ("POSTCOMML", "L_POSTCOMM", "PO_NAME_L")),
    FieldDef("PostComm_R", CONDITIONAL, T_STRING, 40, "Right postal community name", ("POSTCOMMR", "R_POSTCOMM", "PO_NAME_R")),
    FieldDef("RoadClass", CONDITIONAL, T_STRING, 15, "Road class (e.g. Primary, Secondary, Local)", ("ROAD_CLASS", "CLASS", "FCLASS", "MTFCC")),
    FieldDef("OneWay", CONDITIONAL, T_STRING, 2, "One-way: B (both), FT (from-to), TF (to-from)", ("ONE_WAY", "ONEWAY", "DIRONSIGN")),
    FieldDef("SpeedLimit", CONDITIONAL, T_INT, 0, "Posted speed limit (MPH)", ("SPEED", "SPD_LIM", "SPEED_LIM", "SPEEDLIM", "SPEED_LIMIT", "MAXSPEED")),
    FieldDef("Valid_L", OPTIONAL, T_STRING, 1, "Left side valid for routing/geocoding (Y/N)", ("VALIDL",)),
    FieldDef("Valid_R", OPTIONAL, T_STRING, 1, "Right side valid for routing/geocoding (Y/N)", ("VALIDR",)),
    # plugin link field (not part of NENA-STA-006): the master street name this segment uses
    FieldDef("StreetID", OPTIONAL, T_STRING, 38, "Plugin link to the master street names table (not a NENA field)",
             ("STREET_ID", "STREETGUID", "ST_ID")),
]

FIELDS_BY_NAME = {f.name: f for f in RCL_FIELDS}

# Logical groups the plugin depends on.
RANGE_FIELDS = ("FromAddr_L", "ToAddr_L", "FromAddr_R", "ToAddr_R")
PARITY_FIELDS = ("Parity_L", "Parity_R")
STREET_NAME_FIELDS = ("St_PreMod", "St_PreDir", "St_PreTyp", "St_PreSep", "St_Name",
                      "St_PosTyp", "St_PosDir", "St_PosMod")
SIDE_FIELDS = {
    "L": {"from": "FromAddr_L", "to": "ToAddr_L", "parity": "Parity_L"},
    "R": {"from": "FromAddr_R", "to": "ToAddr_R", "parity": "Parity_R"},
}

# Domains -------------------------------------------------------------------
PARITY_DOMAIN = ("O", "E", "B", "Z")
ONEWAY_DOMAIN = ("B", "FT", "TF")
DIRECTIONAL_DOMAIN = ("North", "South", "East", "West", "Northeast", "Northwest",
                      "Southeast", "Southwest")
# Common abbreviations that are NOT allowed in NG9-1-1 (spelled-out required).
DIRECTIONAL_ABBREVIATIONS = {
    "N": "North", "S": "South", "E": "East", "W": "West",
    "NE": "Northeast", "NW": "Northwest", "SE": "Southeast", "SW": "Southwest",
}
YES_NO_DOMAIN = ("Y", "N")

NGUID_URN_RE = re.compile(r"^urn:emergency:uid:gis:RCL:[^:\s]+:[^:\s]+$", re.IGNORECASE)
NGUID_LEGACY_RE = re.compile(r"^RCL_[^@\s]+@[^@\s]+$")


def normalize_name(name: str) -> str:
    """Upper-case alphanumeric form used for fuzzy field matching."""
    return re.sub(r"[^0-9A-Za-z]", "", name or "").upper()


def auto_map(layer_field_names: List[str], field_defs: Optional[List[FieldDef]] = None) -> dict:
    """
    Suggest a NENA -> layer-field mapping from a list of layer field names.

    Exact (case-insensitive) NENA names win, then known aliases. Each layer
    field is used at most once. ``field_defs`` defaults to the built-in
    schema; pass the effective (template) field list to include its names.
    """
    defs = field_defs if field_defs is not None else RCL_FIELDS
    by_norm = {}
    for lf in layer_field_names:
        by_norm.setdefault(normalize_name(lf), lf)
    used = set()
    result = {}
    # pass 1: exact NENA names
    for fd in defs:
        cand = by_norm.get(normalize_name(fd.name))
        if cand and cand not in used:
            result[fd.name] = cand
            used.add(cand)
    # pass 2: aliases (template names come first)
    for fd in defs:
        if fd.name in result:
            continue
        for alias in fd.aliases:
            cand = by_norm.get(normalize_name(alias))
            if cand and cand not in used:
                result[fd.name] = cand
                used.add(cand)
                break
    return result


def match_nena(name: str, base: Optional[List[FieldDef]] = None) -> Optional[str]:
    """NENA logical name for a physical field name (exact name or known alias), else None."""
    defs = base if base is not None else RCL_FIELDS
    n = normalize_name(name)
    for fd in defs:
        if normalize_name(fd.name) == n:
            return fd.name
    for fd in defs:
        if any(normalize_name(a) == n for a in fd.aliases):
            return fd.name
    return None


def mandatory_fields() -> List[str]:
    return [f.name for f in RCL_FIELDS if f.req == MANDATORY]


def get_def(name: str) -> Optional[FieldDef]:
    return FIELDS_BY_NAME.get(name)
