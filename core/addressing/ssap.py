# -*- coding: utf-8 -*-
"""
NENA Site/Structure Address Point (SSAP) schema – built in.

Based on NENA-STA-006.2. As with the centerline schema, verify the M/C/O
flags against the edition your authority uses; a state template can be
loaded instead (Addresses tab) and is merged with this list the same way as
centerline templates.

The last block holds the plugin's own link fields (not NENA): they tie an
address to its master street, the segment and the access point it was
addressed from. NG9-1-1 exports simply leave them out.
"""

from ..schema import CONDITIONAL as C, MANDATORY as M, OPTIONAL as OPT, T_DATETIME as DT, T_INT as INT, \
    T_STRING as S, T_DOUBLE as D, LAST_UPDATED_BY, WHO_ENTERED, FieldDef

SSAP_FIELDS = [
    FieldDef("DiscrpAgID", M, S, 100, "Discrepancy Agency ID"),
    FieldDef("DateUpdate", M, DT, 0, "Date the record was last updated", ("LASTUPDATE", "EDITDATE")),
    WHO_ENTERED,
    LAST_UPDATED_BY,
    FieldDef("Effective", OPT, DT, 0, "Effective date"),
    FieldDef("Expire", OPT, DT, 0, "Expiration date"),
    FieldDef("Site_NGUID", M, S, 254, "Site/structure address point NENA Globally Unique ID",
             ("SITE_NGUID", "NGUID", "ADDR_NGUID")),
    FieldDef("Country", M, S, 2, "Country (ISO 3166-1 alpha-2)"),
    FieldDef("State", M, S, 2, "State (2-letter USPS)"),
    FieldDef("County", M, S, 100, "County (full name)"),
    FieldDef("AddCode", C, S, 6, "Additional code"),
    FieldDef("AddDataURI", OPT, S, 254, "Additional data URI"),
    FieldDef("Inc_Muni", M, S, 100, "Incorporated municipality", ("INCMUNI", "CITY", "MUNI")),
    FieldDef("Uninc_Comm", OPT, S, 100, "Unincorporated community", ("UNINCCOMM",)),
    FieldDef("Nbrhd_Comm", OPT, S, 100, "Neighborhood community", ("NBRHDCOMM",)),
    FieldDef("AddNum_Pre", OPT, S, 15, "Address number prefix", ("ADDNUMPRE",)),
    FieldDef("Add_Number", C, INT, 0, "Address number", ("ADD_NUMBER", "ADDNUM", "HOUSENUM", "HOUSE_NUM", "NUMBER",
                                                      "ADDRNUM", "ST_NUM")),
    FieldDef("AddNum_Suf", OPT, S, 15, "Address number suffix (A, B, 1/2)", ("ADDNUMSUF", "NUM_SUF", "HOUSESUF")),
    FieldDef("St_PreMod", OPT, S, 15, "Street name pre modifier"),
    FieldDef("St_PreDir", C, S, 9, "Street name pre directional", ("PREDIR", "PRE_DIR")),
    FieldDef("St_PreTyp", C, S, 35, "Street name pre type", ("PRETYPE",)),
    FieldDef("St_PreSep", C, S, 20, "Street name pre type separator"),
    FieldDef("St_Name", C, S, 60, "Street name", ("STREETNAME", "STNAME", "STREET")),
    FieldDef("St_PosTyp", C, S, 35, "Street name post type", ("SUFFIX", "STTYPE", "ST_TYPE")),
    FieldDef("St_PosDir", C, S, 9, "Street name post directional", ("POSTDIR", "SUFDIR")),
    FieldDef("St_PosMod", OPT, S, 25, "Street name post modifier"),
    FieldDef("FullName", OPT, S, 254, "Full road name",
             ("FULLNAME", "FULL_NAME", "FULL_STREET", "STREET_FULL", "FULLROAD", "FULL_ROAD",
              "FULLROADNAME", "FULL_ROAD_NAME")),
    FieldDef("ESN", C, S, 5, "Emergency Service Number"),
    FieldDef("MSAGComm", C, S, 30, "MSAG community name", ("MSAG", "MSAG_COMM")),
    FieldDef("Post_Comm", C, S, 40, "Postal community name", ("POSTCOMM", "PO_NAME")),
    FieldDef("Post_Code", C, S, 7, "Postal (ZIP) code", ("ZIP", "ZIPCODE", "POSTCODE")),
    FieldDef("Post_Code4", OPT, S, 4, "ZIP+4"),
    FieldDef("Building", OPT, S, 75, "Building"),
    FieldDef("Floor", OPT, S, 75, "Floor"),
    FieldDef("Unit", OPT, S, 75, "Unit", ("APT", "SUITE", "UNIT_NUM")),
    FieldDef("Room", OPT, S, 75, "Room"),
    FieldDef("Seat", OPT, S, 75, "Seat"),
    FieldDef("Addtl_Loc", OPT, S, 225, "Additional location information"),
    FieldDef("LandmkName", OPT, S, 150, "Complete landmark name"),
    FieldDef("Mile_Post", OPT, S, 150, "Mile post"),
    FieldDef("Place_Type", OPT, S, 50, "Place type"),
    FieldDef("Placement", OPT, S, 25, "Placement method (Structure, Site, Property Access …)"),
    FieldDef("Longitude", OPT, D, 0, "Longitude (WGS84)"),
    FieldDef("Latitude", OPT, D, 0, "Latitude (WGS84)"),
    FieldDef("Elevation", OPT, INT, 0, "Elevation"),
    # plugin link fields (not NENA)
    FieldDef("StreetID", OPT, S, 38, "Plugin: master street name link"),
    FieldDef("RCL_NGUID", OPT, S, 254, "Plugin: centerline segment it was addressed from"),
    FieldDef("Access_X", OPT, D, 0, "Plugin: access point X (layer CRS)"),
    FieldDef("Access_Y", OPT, D, 0, "Plugin: access point Y (layer CRS)"),
    FieldDef("Addr_Side", OPT, S, 1, "Plugin: side of the road (L/R)"),
    FieldDef("AccessPointID", OPT, S, 38, "Plugin: access point this address is reached from",
             ("ACCESS_ID", "AP_ID", "ACCESSPOINTID")),
    FieldDef("UnitType", OPT, S, 32, "Plugin: unit type code (Apt, Suite, …)", ("UNIT_TYPE", "UNITTYPE")),
    FieldDef("AddressStatus", OPT, S, 32, "Plugin: address status code", ("ADDR_STATUS", "ADDRESSSTATUS")),
    FieldDef("AddressType", OPT, S, 32, "Plugin: what this address represents", ("ADDR_TYPE", "ADDRESSTYPE")),
]

DRIVEWAY_FIELDS = [
    FieldDef("DW_NGUID", OPT, S, 254, "Driveway id"),
    FieldDef("Site_NGUID", OPT, S, 254, "Address point served"),
    FieldDef("RCL_NGUID", OPT, S, 254, "Centerline segment it connects to"),
    FieldDef("StreetID", OPT, S, 38, "Master street name"),
    FieldDef("Parent_DW", OPT, S, 254, "Shared driveway this spur branches from"),
    FieldDef("AccessPointID", OPT, S, 38, "Access point this driveway leaves", ("ACCESS_ID", "AP_ID")),
    FieldDef("DateUpdate", OPT, DT, 0, "Date updated"),
    WHO_ENTERED,
    LAST_UPDATED_BY,
]

ACCESS_FIELDS = [
    FieldDef("AccessPointID", OPT, S, 38, "Access point id"),
    FieldDef("RCL_NGUID", OPT, S, 254, "Centerline segment the access leaves"),
    FieldDef("StreetID", OPT, S, 38, "Master street name"),
    FieldDef("RoadSide", OPT, S, 1, "Side of the road (L/R)"),
    FieldDef("DateUpdate", OPT, DT, 0, "Date updated"),
    WHO_ENTERED,
    LAST_UPDATED_BY,
    FieldDef("Notes", OPT, S, 254, "Notes"),
]

PLACEMENT_VALUES = ("Structure", "Site", "Property Access", "Parcel", "Geocoding", "Unknown")

# Shown as their own layer-and-field choices. Each one is still an attribution rule.
PRIMARY_BOUNDARIES = (
    ("PostCode", "ZIP code"),
    ("MSAGComm", "MSAG community"),
    ("ESN", "ESN"),
)

# community keys -> (address field, centerline left field, centerline right field)
COMMUNITY_FIELDS = {
    "MSAGComm": ("MSAGComm", "MSAGComm_L", "MSAGComm_R"),
    "ESN": ("ESN", "ESN_L", "ESN_R"),
    "PostCode": ("Post_Code", "PostCode_L", "PostCode_R"),
    "PostComm": ("Post_Comm", "PostComm_L", "PostComm_R"),
    "IncMuni": ("Inc_Muni", "IncMuni_L", "IncMuni_R"),
    "UnincCom": ("Uninc_Comm", "UnincCom_L", "UnincCom_R"),
    "NbrhdCom": ("Nbrhd_Comm", "NbrhdCom_L", "NbrhdCom_R"),
    "County": ("County", "County_L", "County_R"),
    "State": ("State", "State_L", "State_R"),
    "Country": ("Country", "Country_L", "Country_R"),
    "AddCode": ("AddCode", "AddCode_L", "AddCode_R"),
}
