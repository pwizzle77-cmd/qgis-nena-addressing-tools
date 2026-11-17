# Recommended NENA Road Centerline schema

The plugin's built-in schema (`core/schema.py`) follows the Road Centerline
layer of **NENA-STA-006.2 (NG9-1-1 GIS Data Model)**. `M` = mandatory,
`C` = conditional (required when the condition applies, e.g. the road has a
name), `O` = optional. Widths are the suggested text lengths used when the
plugin creates fields ("Add missing fields", "New template layer", and
**Create schema**). Create schema also writes the street-name table, the
site/structure address points, the driveways, the access points, and the
lookup tables (road type, unit type, address status, road status, and address
type) into a GeoPackage, a File Geodatabase, or a database connection. An
address stores `AccessPointID`. An access point is not removed while an
address or driveway still stores that id. The same dialog can also create a
version table for each geometry layer and the street-name table: the live fields, plus the plugin's
version columns (`ArchiveID`, `Version`, `ChangeType`, `ChangeDate`,
`ChangedBy`, `FeatureKey`, `SourceFid`, `ChangedFields`, `OldValues`,
`NewValues`). Each saved change is one version of that feature. Those columns
are not part of NENA-STA-006. See the README, section 2d.

> Verify the M/C/O status and widths against the edition of NENA-STA-006 and
> any state-specific profile your 9-1-1 authority has adopted. The flags are in
> one list in `core/schema.py` and are easy to adjust.

| NENA field | Req | Type | Width | Description | Auto-map aliases (first 5) |
|---|---|---|---|---|---|
| `DiscrpAgID` | M | Text | 100 | Discrepancy Agency ID (domain of the agency that resolves discrepancies) | `DISCRPAGID`, `DISCREPANCY_AGENCY` |
| `DateUpdate` | M | DateTime |  | Date the record was last updated | `DATE_UPD`, `LASTUPDATE`, `LAST_EDITED_DATE`, `EDITDATE`, `UPDATED` |
| `WhoEntered` | O | Text | 100 | User who first saved the record. On a database layer, a save fills this from the connection login when it is empty. | `WHO_ENTERED`, `ENTEREDBY`, `CREATEDBY`, `CREATED_BY`, `ADDUSER` |
| `LastUpdatedBy` | O | Text | 100 | User who last saved the record. On a database layer, a save writes the connection login. | `LAST_UPDATED_BY`, `UPDATEDBY`, `LASTEDITEDBY`, `LAST_EDITED_USER`, `EDITEDBY` |
| `Effective` | O | DateTime |  | Date the record becomes effective | `EFF_DATE` |
| `Expire` | O | DateTime |  | Date the record expires | `EXP_DATE` |
| `RCL_NGUID` | M | Text | 254 | Road Centerline NENA Globally Unique ID | `NGUID`, `RCLNGUID`, `RCL_NGUID`, `SEGMENT_ID` |
| `AdNumPre_L` | O | Text | 15 | Left address number prefix | `ADNUMPREL` |
| `AdNumPre_R` | O | Text | 15 | Right address number prefix | `ADNUMPRER` |
| `FromAddr_L` | M | Integer |  | Left FROM address (at the start vertex) | `L_F_ADD`, `LFROMADD`, `L_ADD_FROM`, `FROMLEFT`, `LEFTFROM` |
| `ToAddr_L` | M | Integer |  | Left TO address (at the end vertex) | `L_T_ADD`, `LTOADD`, `L_ADD_TO`, `TOLEFT`, `LEFTTO` |
| `FromAddr_R` | M | Integer |  | Right FROM address (at the start vertex) | `R_F_ADD`, `RFROMADD`, `R_ADD_FROM`, `FROMRIGHT`, `RIGHTFROM` |
| `ToAddr_R` | M | Integer |  | Right TO address (at the end vertex) | `R_T_ADD`, `RTOADD`, `R_ADD_TO`, `TORIGHT`, `RIGHTTO` |
| `Parity_L` | M | Text | 1 | Left parity: O(dd) E(ven) B(oth) Z(ero) | `PARITYL`, `L_PARITY`, `PARITY_LEFT` |
| `Parity_R` | M | Text | 1 | Right parity: O(dd) E(ven) B(oth) Z(ero) | `PARITYR`, `R_PARITY`, `PARITY_RIGHT` |
| `St_PreMod` | O | Text | 15 | Street name pre modifier | `PREMOD` |
| `St_PreDir` | C | Text | 9 | Street name pre directional (spelled out) | `PREDIR`, `PRE_DIR`, `PREFIX`, `DIRPRE`, `PREFIXDIR` |
| `St_PreTyp` | C | Text | 35 | Street name pre type | `PRETYPE`, `PRE_TYPE`, `PRETYP`, `FETYPEP` |
| `St_PreSep` | C | Text | 20 | Street name pre type separator | `PRESEP` |
| `St_Name` | C | Text | 60 | Street name | `STREETNAME`, `STNAME`, `STR_NAME`, `STREET`, `NAME` |
| `St_PosTyp` | C | Text | 35 | Street name post type (suffix) | `SUFFIX`, `STTYPE`, `ST_TYPE`, `POSTTYPE`, `POST_TYPE` |
| `St_PosDir` | C | Text | 9 | Street name post directional (spelled out) | `POSTDIR`, `POST_DIR`, `SUFDIR`, `SUF_DIR`, `SUFFIXDIR` |
| `St_PosMod` | O | Text | 25 | Street name post modifier | `POSTMOD` |
| `ESN_L` | C | Text | 5 | Left Emergency Service Number | `ESNL`, `L_ESN` |
| `ESN_R` | C | Text | 5 | Right Emergency Service Number | `ESNR`, `R_ESN` |
| `MSAGComm_L` | C | Text | 30 | Left MSAG community name | `MSAG_L`, `L_MSAG`, `MSAGCOMML`, `MSAGL` |
| `MSAGComm_R` | C | Text | 30 | Right MSAG community name | `MSAG_R`, `R_MSAG`, `MSAGCOMMR`, `MSAGR` |
| `Country_L` | M | Text | 2 | Left country (ISO 3166-1 alpha-2, e.g. US) | `COUNTRYL` |
| `Country_R` | M | Text | 2 | Right country (ISO 3166-1 alpha-2, e.g. US) | `COUNTRYR` |
| `State_L` | M | Text | 2 | Left state (2-letter USPS) | `STATEL`, `L_STATE` |
| `State_R` | M | Text | 2 | Right state (2-letter USPS) | `STATER`, `R_STATE` |
| `County_L` | M | Text | 100 | Left county (full name) | `COUNTYL`, `L_COUNTY` |
| `County_R` | M | Text | 100 | Right county (full name) | `COUNTYR`, `R_COUNTY` |
| `AddCode_L` | C | Text | 6 | Left additional code | `ADDCODEL` |
| `AddCode_R` | C | Text | 6 | Right additional code | `ADDCODER` |
| `IncMuni_L` | M | Text | 100 | Left incorporated municipality | `INCMUNIL`, `L_MUNI`, `MUNI_L`, `CITY_L` |
| `IncMuni_R` | M | Text | 100 | Right incorporated municipality | `INCMUNIR`, `R_MUNI`, `MUNI_R`, `CITY_R` |
| `UnincCom_L` | O | Text | 100 | Left unincorporated community | `UNINCCOML` |
| `UnincCom_R` | O | Text | 100 | Right unincorporated community | `UNINCCOMR` |
| `NbrhdCom_L` | O | Text | 100 | Left neighborhood community | `NBRHDCOML` |
| `NbrhdCom_R` | O | Text | 100 | Right neighborhood community | `NBRHDCOMR` |
| `PostCode_L` | C | Text | 7 | Left postal (ZIP) code | `ZIP_L`, `L_ZIP`, `ZIPL`, `ZIPLEFT`, `POSTCODEL` |
| `PostCode_R` | C | Text | 7 | Right postal (ZIP) code | `ZIP_R`, `R_ZIP`, `ZIPR`, `ZIPRIGHT`, `POSTCODER` |
| `PostComm_L` | C | Text | 40 | Left postal community name | `POSTCOMML`, `L_POSTCOMM`, `PO_NAME_L` |
| `PostComm_R` | C | Text | 40 | Right postal community name | `POSTCOMMR`, `R_POSTCOMM`, `PO_NAME_R` |
| `RoadClass` | C | Text | 15 | Road class (e.g. Primary, Secondary, Local) | `ROAD_CLASS`, `CLASS`, `FCLASS`, `MTFCC` |
| `OneWay` | C | Text | 2 | One-way: B (both), FT (from-to), TF (to-from) | `ONE_WAY`, `ONEWAY`, `DIRONSIGN` |
| `SpeedLimit` | C | Integer |  | Posted speed limit (MPH) | `SPEED`, `SPD_LIM`, `SPEED_LIM`, `SPEEDLIM`, `SPEED_LIMIT` |
| `Valid_L` | O | Text | 1 | Left side valid for routing/geocoding (Y/N) | `VALIDL` |
| `Valid_R` | O | Text | 1 | Right side valid for routing/geocoding (Y/N) | `VALIDR` |
| `StreetID` | O | Text | 38 | Plugin link to the master street names table (not a NENA field) | `STREET_ID`, `STREETGUID`, `ST_ID` |
| `RoadStatus` | O | Text | 32 | Plugin road-status code from the RoadStatus lookup | `ROAD_STATUS`, `ROADSTATUS`, `RD_STATUS` |

## Access points and lookup lists

`AccessPoint` is a point layer. Its fields are `AccessPointID`, `RCL_NGUID`, `StreetID`, `RoadSide`, `DateUpdate`, `WhoEntered`, `LastUpdatedBy`, and `Notes`. Place address writes the point 10 feet straight off the road click, on the building's side. `RCL_NGUID` is the centerline, and the address stores `AccessPointID`. One access point can be shared. Deleting it is blocked while any address or driveway still stores that id.

Address points also store `UnitType`, `AddressStatus`, and `AddressType`. Those three store the lookup **code**. `St_PosTyp` and `St_PreTyp` store the spelled road-type **label** (Street, Lane, Court) so the full road name stays readable.

The lookup tables `RoadType`, `UnitType`, `AddressStatus`, `RoadStatus`, and `AddressType` have no geometry. Each row is `Code`, `Label`, `SortOrder`, `Active` (`Y` or `N`), and `Notes`. Create schema fills them with the default lists. They are not versioned.

## Domains enforced by validation

| Field | Allowed values |
|---|---|
| `Parity_L`, `Parity_R` | `O` odd, `E` even, `B` both, `Z` zero / no addresses (range must be 0–0) |
| `OneWay` | `B` both directions, `FT` from→to (digitized direction), `TF` to→from |
| `St_PreDir`, `St_PosDir` | `North` `South` `East` `West` `Northeast` `Northwest` `Southeast` `Southwest` (spelled out; `N`, `SW`… are flagged) |
| `SpeedLimit` | integer 1–100 (multiples of 5 expected) |
| `RCL_NGUID` | `urn:emergency:uid:gis:RCL:{unique id}:{agency}` (legacy `RCL_{id}@{agency}` reported as info) |

## Range conventions used by the plugin

* **Digitized direction = increasing addresses.** `FromAddr_*` is the number at
  the first vertex, `ToAddr_*` at the last vertex.
* **Left / right** are relative to the digitized direction.
* Default parity: **odd on the left, even on the right** (change per layer in
  Ranges, Method; existing valid `Parity_*` values are kept by default).
* A side with no addresses is `0`–`0` with parity `Z`.
* Ranges on one street never share a number, regardless of side (the overlap
  check pools left and right).

## Default ranging settings

| Setting | Default | Notes |
|---|---|---|
| Method | Hundred-block | One block per intersection-to-intersection segment |
| Scope | Street chains | Connected segments with the same street key, in digitized order |
| Start numbering | Continue from upstream | Fallback: first segment's existing FROM, then base address |
| Base address | 100 | |
| Block size | 100 | 1000 for some rural grids |
| Interval | 5.28 ft per number | = 1000 numbers per mile (3.2 m ≈ 500 per km) |
| Proportional source | Chain's existing min/max | Or a fixed FROM/TO range |
| Address-point search distance | 60 m | Points snap to the nearest line (same street name if mapped) |
| Connectivity tolerance | 0.5 m | Endpoints closer than this are connected |
| Overwrite existing | on | Off = only fill empty / 0–0 features |
| Write parity / stamp DateUpdate | on / on | |

See `examples/nena_rcl_config_example.json` for the full default configuration
with a 1:1 NENA field mapping, and `examples/legacy_shapefile_mapping.json`
for a mapping onto typical legacy names (`L_F_ADD`, `STREETNAME`, …).
