# NENA Addressing Tools — QGIS plugin

Create and maintain **NENA NG9-1-1 street names and road centerline** address
ranges. Requires QGIS 3.34 or newer, including the 3.40 LTR and QGIS 4.
No extra Python packages. License: [GPL-2.0-or-later](LICENSE).
Repository: https://github.com/pwizzle77-cmd/qgis-nena-addressing-tools

* **Addressing tool**: click the road at the driveway entrance, (optionally)
  the driveway's bends, then the rooftop – the number is calculated from
  where the driveway meets the road, the master street name is used, and
  MSAG, ZIP, ESN, and the other side values are copied from the left or right
  side of the centerline. Those side fields are calculated from the polygon
  beside each side of the road. The address point and driveway are created
  together.
* **Master street names**: one table of road names (GeoPackage or any database
  connection) that centerlines and address points link to by `StreetID`.
  Renames cascade to every linked feature, duplicates are blocked by a
  configurable rule (county-wide, full road name by default), and legacy
  spellings are cleaned up when the list is built from the centerlines.

* **Fresh schema**: create the road centerlines, master street names, address
  points, and driveways together in a GeoPackage, a File Geodatabase, or a
  database connection (PostgreSQL, SQL Server, Oracle, and the other
  connections configured in QGIS).
* **Import** existing roads, street names, addresses, and driveways into that
  schema. Fields are matched by NENA name and the usual legacy names
  (`L_F_ADD`, `STREETNAME`, `ZIP`, …), and you can change the match before
  the copy. The copy is saved into the destination immediately.
* **Change tracking**: optional version tables for those four layers, in the
  same GeoPackage, File Geodatabase, or database. With **Track changes** on
  (**Settings ▸ Project**), each save adds a numbered version of the feature,
  including the old and new values of an update.
* **Field mapping** of NENA-STA-006 attributes onto *any* line layer
  (GeoPackage, shapefile, PostGIS, SpatiaLite, memory…), stored on the layer
  or in a JSON file.
* **Auto-ranging** of `FromAddr_L / ToAddr_L / FromAddr_R / ToAddr_R` with
  parity: hundred-block, interval (distance per number), proportional along
  length, or derived from address points.
* **Auto-split at new intersections** (default on): digitizing a new road
  that ends on or crosses an existing centerline splits that centerline and
  re-ranges it so the new section continues the previous section's numbering.
  **Split existing roads at intersections** does the same pass for a file
  that was imported with roads already crossing.
* **Side changes and range gaps**: split a centerline where the ZIP, MSAG,
  or ESN changes along it, and extend a segment so its numbers meet the next
  one. **Compare centerlines to MSAG** checks street, community, low–high,
  and odd/even against the telephone company's extract.
* **Default values** for state, county, one-way, parity, and any other field
  that rarely changes. They fill empty fields only.
* **Database login**: on PostgreSQL, SQL Server, Oracle, and SAP HANA, a save
  writes the connection's user into `WhoEntered` when that field is empty and
  into `LastUpdatedBy` on each new or changed feature. Change tracking stores
  the same login in `ChangedBy`.
* **Re-ranging on split / merge**: after QGIS *Split Features* — or with the
  plugin's own **Split & Re-range** map tool (one click, one undo step) — the
  pieces get proportional ranges **anchored to existing address points**, so
  a point next to the split keeps its number on the piece it lies along.
  After *Merge Selected Features* ranges are combined. Optional TO recompute
  after reshapes.
* **NENA field templates**: load your state's NENA GIS Data Model template
  (GeoPackage, shapefile, GeoJSON, a layer in the project) or a JSON/CSV
  schema; its field names, types, widths and M/C/O flags drive mapping, field
  creation and validation.
* **Validation**: gaps, overlaps, parity, reversed/half-zero ranges,
  digitizing direction conflicts, domains, NGUID format and duplicates,
  geometry. Results table, select/zoom, CSV export, review layer, and
  **one-click selection of every feature with overlapping ranges**.
* Everything goes through the **edit buffer** as named, undoable commands.
  Nothing is written to disk until you save the layer.
* Pure PyQGIS. No third-party Python packages.

---

## 1. Installation

This repository is the plugin. QGIS loads it only when the folder that
contains `metadata.txt` is named `nena_rcl_tools`.

1. Copy this folder, or clone it, and name that copy `nena_rcl_tools`.
2. Put it in the QGIS profile plugin folder:

| QGIS | Windows | macOS | Linux |
|---|---|---|---|
| 4 | `%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins\nena_rcl_tools` | `~/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins/nena_rcl_tools` | `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/nena_rcl_tools` |
| 3.34+ | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\nena_rcl_tools` | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/nena_rcl_tools` | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/nena_rcl_tools` |

3. Restart QGIS, or use Plugin Reloader.
4. Open **Plugins → Manage and Install Plugins → Installed** and enable **NENA Addressing Tools**.

**From ZIP.** Zip the folder so the zip contains `nena_rcl_tools/metadata.txt` at the top level. Then use *Plugins ▸ Manage and Install Plugins ▸ Install from ZIP*.

No resource compilation is required. Toolbar icons are the SVG files in `icons/`.

After enabling, you get a **NENA Addressing Tools** toolbar and a
*Vector ▸ NENA Addressing Tools* menu:

| Icon | Action |
|---|---|
| 911 badge | Show / hide the NENA Addressing panel |
| street sign | Open the **Streets** tab (master street names) |
| house + driveway | **Address & driveway tool** – click road, bends, rooftop |
| house + undo arrow | **Undo last address** (address point and its driveway together) |
| 101 → 199 | Auto-range the **selected** centerlines with the current settings |
| arc arrow | Redistribute the combined range across the selected segments |
| red split mark | **Split & Re-range** map tool – click a centerline to split and re-range it |
| scissors line | Toggle automatic re-ranging after split/merge for the panel's layer |
| shield | Validate the whole layer (features with overlapping ranges are selected) |
| overlapping bars | **Find & select overlapping address ranges** (runs only the overlap check) |

The *Vector ▸ NENA Addressing Tools* menu also has **Create NENA schema**,
**Import into NENA schema**, and **Create version tables**. On the panel those
live under **Settings ▸ Project**, with **Track changes**. The Streets and
Addresses *Create* menus include the same schema, import, and version actions.

The panel has five tabs. **Save** at the top stores the panel settings. It does
not save edits on the layers.

| Tab | What you do there |
|---|---|
| **Streets** | Master names, and the roads and addresses that use them |
| **Addresses** | Place an address, and choose the address and driveway layers |
| **Ranges** | Give roads address ranges, fill gaps, and split them. The method is on **Ranges ▸ Method** |
| **Check** | Gaps, overlaps, and the MSAG comparison. Results open on **Check ▸ Results** |
| **Settings** | Schema, field mapping, sides of the road, defaults, street and address rules, and edit behaviour |

**Settings** has one page per job:

| Page | What you set |
|---|---|
| **Project** | **Create schema…**, **Import…**, **Versions…**, and **Track changes** |
| **Fields** | Match NENA fields to the centerline, including the street key |
| **Sides** | ZIP, MSAG, and ESN polygons, **Calculate sides**, and **Split where the side changes** |
| **Defaults** | State, county, one-way, parity, and any other value that fills empty fields only |
| **Street rules** | How names must be unique, and what a rename does |
| **Address rules** | How a new address gets its number, and per-community overrides |
| **Editing** | Re-range after a split or merge, new intersections, and NGUID |

**Ranges** is split into **Run** (range, fill gaps, split) and **Method** (how numbers are assigned). **Check** is split into **Results**, **Checks**, and **MSAG**. **Save** writes the panel settings onto the centerline layer and into the project. Save the layers themselves with QGIS when you want the edits kept.

---

## 2. Configuration and field mapping (panel ▸ *Settings, Fields*)

1. Open the panel and pick the **centerline layer** (the panel also follows
   the active line layer in the Layers panel).
2. If the layer has no stored configuration, fields are **auto-mapped** by
   NENA name and common legacy names (`L_F_ADD`, `FROMLEFT`, `STREETNAME`,
   `PREDIR`, `SUFFIX`, `ONE_WAY`, …). Review the combos; unmapped mandatory
   fields are shown in bold.
3. Optional: **Add mandatory** / **Add all fields** creates them on the layer
   with standard NENA names and types (needs an editable provider; NENA names
   are ≤ 10 characters so they also work in shapefiles).
4. **Street key** (default: all `St_*` name parts) decides which
   segments belong to the same street for chaining and overlap checks.
5. **Save**. The configuration is a layer *custom property*
   (`nena_rcl/config`), so it is saved in the `.qgz` project, in layer
   definition files (`.qlr`) and in QML styles that include *Custom
   Properties*. Use **Export / Import JSON** to reuse it across projects or
   to keep it next to the data.

The minimum mapping for ranging is the four range fields. Parity, street name
fields, `RCL_NGUID` and `DateUpdate` are used when mapped.

**Who entered a record.** `WhoEntered` and `LastUpdatedBy` are optional. When
the layer comes from a database connection, saving writes that connection's
login into an empty `WhoEntered` on a new feature and into `LastUpdatedBy` on
every new or changed feature. A value already in `WhoEntered` stays, including
on a split piece that copied it. A GeoPackage, File Geodatabase, or shapefile
has no connection login, so those fields are left as they are. The same login
is stored in `ChangedBy` when change tracking is on. The columns are recognized
by name (`WhoEntered`, `CreatedBy`, `LastUpdatedBy`, `LastEditedBy`, and the
usual spellings) and do not have to be mapped first.

**Values that rarely change.** State, county, country, one-way, parity, and
any other field you add get a default under **Settings ▸ Defaults**. **Fill empty fields**
writes them where the feature is blank, and a new road or a split piece does
the same. A value already on the feature is not replaced. Leave parity blank
when ranging should choose odd and even. A new address uses the default for
its side when that side of the road is empty.

A full recommended schema with M/C/O flags, domains and defaults is in
[`docs/NENA_SCHEMA.md`](docs/NENA_SCHEMA.md). **Empty layer** creates an
empty in-memory layer with that schema (save it with *Export ▸ Save Features
As… ▸ GeoPackage*). **Create schema** writes the centerlines together with the
street names, address points, and driveways into a file or database (section 2d).

### Field templates

*Field template ▾* (top of **Settings ▸ Fields**) loads the list of fields your
layer is supposed to have:

| Source | Notes |
|---|---|
| GeoPackage / shapefile / GeoJSON / FlatGeobuf … | The field list of the layer is read. If a GeoPackage holds several layers, the road centerline layer is picked automatically (name contains *centerline*, *rcl* or *road*) or you are asked. |
| A layer in the project | Use for File Geodatabase templates, PostGIS tables etc. – load the layer into QGIS first. |
| JSON | `{"name": "...", "fields": [{"name", "type", "length", "req", "description", "nena"}]}` – see `examples/state_profile_template_example.json`. |
| CSV | Columns `name,type,length,req,description,nena` (only `name` required) – see `examples/template_example.csv`. |

Template fields are matched to NENA attributes by name or known alias
(`L_F_ADD` → `FromAddr_L` …) or explicitly via the `nena` column. Matched
fields take the template's **physical name, type, width and M/C/O flag**;
fields that don't match (state extensions such as a statewide segment id)
are added as extra rows. After loading, the plugin auto-maps and offers to
**add the template fields missing on your layer** with the template's own
names. The template is stored with the layer configuration, is used by
**Add mandatory**, **Add all fields**, and **Empty layer**, and its mandatory fields are
checked by validation. *Export current template as JSON* writes the active
(or built-in) template; *Back to the built-in NENA schema* removes it.

---

## 2b. Master street names (panel ▸ *Streets*)

One table holds every road name. Centerlines and address points carry a
`StreetID` that points at it; their NENA name parts (`St_PreDir`, `St_Name`,
`St_PosTyp` …) stay on the features (NG9-1-1 exports need them) but are kept
in sync with the master.

**1. Set up the layers** (settings are stored in the project):

| Setting | Notes |
|---|---|
| Master street names | Pick an existing table, or *Create ▾* → a new table in a GeoPackage, or in **any database connection configured in QGIS** (PostGIS, SQL Server, Oracle, SAP HANA, SpatiaLite, GeoPackage connections). *Add missing fields* completes an existing table. |
| Address points | Optional now; renames cascade to them too. Their name-part fields are matched by NENA name. |
| Link field | `StreetID` by default. *Add to layers* creates it on the centerline and address layers. |

Master table fields: `StreetID` (GUID), the eight NENA name parts, `FullName`,
`Scope`, `Status` (Active / Proposed / Retired), `AliasOf`, `Effective`,
`Expire`, `DateUpdate`, `Notes`.

**2. Name uniqueness** (**Settings ▸ Street rules**):

| Option | Default | Choices |
|---|---|---|
| Scope | **County-wide** | County-wide, or per value of a field (MSAG community, municipality, postal community, ESN, county) |
| Compare | **Full road name** – directionals, name and types together ("N Main St" ≠ "S Main St" ≠ "N Main Ave") | or *Ignore street type* ("Oak St" = "Oak Ave") |
| Sound-alike | Warn | "Smith Rd" / "Smyth Rd" are reported, never blocked |
| Duplicates | Block | or Warn only |

Abbreviations are normalised before comparing: `N` = `North`, `St` = `Street`,
`Co Rd` = `County Road`, case and periods ignored.

**3. Build from roads…** reads the names on the centerlines, groups the
different spellings of the same road (`Main St`, `MAIN ST`, `Main Street`) and
shows them for review (double-click to pick the spelling to keep; sound-alike
names are listed). *Build* creates one master record per road (re-using
records that already exist, so it can be run again), writes `StreetID` onto
the centerlines and on address points with the same name, and optionally
rewrites their name parts to the chosen spelling. A centerline with an empty
street name is left out. The build does not invent a blank master row.

**4. Day to day**

* **Add…** / **Rename…** – a form with the name parts, a live full-name preview
  and the uniqueness check. Rename shows how many centerlines and addresses
  will change, asks for confirmation, updates them all, stamps `DateUpdate`
  and (optionally) keeps the old name as a *Retired* alias.
* **Retire** – keeps the record for history; validation flags features still using it.
* **Assign to selected** – links the selected segments to the chosen street.
* **StreetID drop-down** – creates QGIS relations so `StreetID` is a searchable
  drop-down of active names in attribute forms and the attribute table.
* **Live sync** – typing a new name part straight into the master table
  cascades to linked features; picking a `StreetID` on a feature copies its
  name parts. Both are separate undo steps and can be switched off under
  **Settings ▸ Street rules** (*When names change*).
* **Check names** – duplicates and sound-alikes in the master, and
  `NAME_OUT_OF_SYNC`, `STREETID_MISSING`, `STREETID_UNKNOWN`, `STREET_RETIRED`
  on centerlines and addresses, with *Select in layer*. The centerline checks
  also run on **Check ▸ Checks** (*Names vs master list*).
* **Sync to features** – copies the master name parts onto every linked feature.

All changes go into the layers' edit buffers. Use *Save All Layers* (or QGIS
transaction mode when the layers share a GeoPackage or database connection)
to commit them together.

---

## 2c. Addressing (panel ▸ *Addresses*)

**Layers.** Choose (or *Create ▾* in a GeoPackage or any database connection)
the **address point** layer and, optionally, the **driveway** layer. New
address layers use the built-in **NENA site/structure address point (SSAP)
schema**; *Template ▾* loads a **state template** instead (GeoPackage,
shapefile, GeoJSON, JSON or CSV – the same loader as for centerlines). Fields
are matched by NENA name and common legacy names. The plugin also uses a few
link fields of its own on address points: `StreetID`, `RCL_NGUID`,
`Access_X`, `Access_Y`, `Addr_Side` (not NENA; leave them out of exports).
`FullName` is filled from the master name when an address is created,
readdressed, assigned, or synced. A full name you typed yourself is kept
until a master rename replaces it.

**The address tool**

1. Click the **centerline where the driveway meets the road** (snapping
   applies). Click an existing **driveway** instead to share it: the new
   address uses that driveway's access point and a spur is drawn from where
   you clicked.
2. Optional clicks follow the **driveway's bends**.
3. **Right-click or double-click the rooftop.** Backspace removes the last
   click, Esc starts over (Esc again leaves the tool).
4. A dialog shows the calculated **number** (editable, with suffix / unit /
   placement), the street name from the master list, the community values and
   any warnings. *Create address* writes the point and the driveway.

**How the number is calculated.** The method, parity, and community overrides
are under **Settings ▸ Address rules**. Placement, the driveway, and the
selection overlay stay on the Addresses tab.

| Step | Rule |
|---|---|
| Side | The rooftop's side of the road, relative to the road direction **at the access point** (works on curves and corner lots) |
| Raw number | **Defined distance (default):** number at the road start + distance from the road start ÷ distance per number (5.28 ft = 1000 per mile). Or **interpolate** within the segment's range on that side, or **fixed frontage** (N numbers every X ft). The road start is the start of the street's chain of segments (same `StreetID`), following digitizing direction; branches continue from where they leave the main line |
| Parity | Rounded (nearest or down) to the side's odd/even – the segment's own `Parity_L/R` wins when set; a `Z` side refuses addresses |
| Order | Kept between the existing addresses on that side so numbers increase along the street |
| Number taken | Next free number, a suffix (101A), or ask – configurable; a suffix is used when no whole number fits |
| Overrides | Per community (MSAG, ESN, ZIP, municipality, county): a different method, distance per number or start number |

The access point is stored on the address (`Access_X`, `Access_Y`) and is the
driveway's first vertex, so no separate access-point table is needed.

**Side of the road.** Under **Settings ▸ Sides**, choose the ZIP, MSAG, and ESN
polygon layer and field. **Calculate sides** writes `PostCode_L` /
`PostCode_R`, `MSAGComm_L` / `MSAGComm_R`, `ESN_L` / `ESN_R`, and the other
side fields from the polygon just beside that side. A road is often the
boundary between two ZIP codes or two communities, which is why the post
office and the MSAG keep a left and a right value. A new address copies the
side it sits on. **Copy road side** (Addresses tab) does the same for
points already drawn. An empty side is calculated when the address is created,
then copied. **Split where the side changes** cuts a centerline at the polygon
boundary, gives each piece its own left and right values, and copies that side
onto the addresses that sit on it. Calculate still reports a crossing and
selects it when you only want to see it.

**Selected addresses on the map.** While address points are selected, a
temporary marker shows where each was addressed: a circle on the centerline
at its access point, a dashed line to the rooftop and its number (plus the
number the current settings would give, when it differs). Blue = on the road,
red = the road has moved away from the stored access point, hollow grey = no
stored access point (inferred). Nothing is written; it disappears when the
selection is cleared.

**Renaming part of a road.** When *Assign to selected centerlines* (Streets
tab) moves segments that carry addresses to another street, the addresses get
the new name and a dialog lists them with their recalculated numbers:
**Keep numbers**, **Re-address all** or **Re-address ticked**.

**Checks** (Addresses ▸ **Check**): `ADDR_OUTSIDE_RANGE`, `ADDR_WRONG_SIDE`
(parity), `ADDR_OUT_OF_ORDER`, `ADDR_DUPLICATE` (number + suffix + unit per
street), `ACCESS_OFF_ROAD`, `ADDR_NO_STREET`, `ADDR_STREET_NOT_ON_MAP`,
`ATTRIB_NO_POLYGON`, `ATTRIB_MULTIPLE`, `ATTRIB_MISMATCH`, and on centerlines
`RCL_CROSSES_BOUNDARY` – with *Select in layer*.

**Undo.** QGIS keeps one undo history per layer; *Undo last address* removes
the last address point **and** its driveway in one go. When the layers share a
GeoPackage or database connection, QGIS transaction mode also saves them
together.

**Reports.** *Streets report* (every master name with status, alias,
segments, addresses, lowest/highest number) and *Addresses by community*
(counts per MSAG, ZIP, ESN, municipality …) as CSV.

**PostgreSQL integrity (Streets tab ▸ Database SQL…).** For PostGIS tables the
plugin generates – and can run – SQL that makes the database enforce the links
for every client: a unique `StreetID`, foreign keys, a trigger that cascades
renames and one that copies the name parts when a `StreetID` is set. With a
GeoPackage the plugin keeps the links itself.

---

## 2d. Fresh schema and import

**Create schema…** (**Settings ▸ Project**, or *Vector ▸ NENA Addressing Tools ▸ Create NENA schema*)
writes an empty schema and makes those layers the panel's centerline, street,
address, and driveway layers.

| Destination | What you pick |
|---|---|
| GeoPackage | A `.gpkg` file. Several tables are added to the same file. |
| File Geodatabase | A folder ending in `.gdb`. QGIS creates it with the OpenFileGDB writer. |
| Database connection | A connection already defined in the Data Source Manager: PostgreSQL, SQL Server, Oracle, SAP HANA, SpatiaLite, or an OGR connection. The database schema (for example `public` or `dbo`) is created when it is not already there. |

Choose one coordinate reference system. It is used for the centerlines, address
points, and driveways. A table prefix is optional (`ng911_RoadCenterline`).
The tables are:

| Table | What it holds |
|---|---|
| `RoadCenterline` | The built-in NENA centerline fields, or the field template loaded under Settings, Fields |
| `StreetNames` | The master street-name list (`StreetID`, name parts, `FullName`, `Status`, …) |
| `SiteStructureAddressPoint` | The built-in site/structure address point fields, or the state template from the Addresses tab, plus `StreetID`, `RCL_NGUID`, `Access_X`, `Access_Y`, `Addr_Side` |
| `Driveway` | Driveway id, the address and centerline it serves, and the master street |

*Replace tables that already exist* overwrites tables of those names. Other
tables in the file or database stay as they are. The new layers are grouped
in the project under `NENA` plus the file or schema name.

Boundary polygons (MSAG, ZIP, ESN, municipal limits) are not created here.
Keep using the polygon layers you already have, and point **Settings ▸ Sides**
at them.

**Import…** copies features from layers already in the project into those
tables. Each schema layer has its own source. The match starts from NENA names
and legacy aliases; **Fields…** changes it. *Import selected features only*
uses each source layer's selection. Geometry is reprojected into the
destination CRS. A line source can fill centerlines or driveways, and a point
source can fill address points. A street-name row with an empty street name
is left out. A road or address is still copied when its name is blank, and
the unmapped mandatory fields are reported rather than invented.

Import saves the features into the destination immediately. The centerline,
address, and driveway tools still put their edits in the edit buffer until
you save those layers.

### Change tracking

**Versions…** (**Settings ▸ Project**, or *Vector ▸ NENA Addressing Tools ▸ Create version tables*)
creates one version table for each layer you check, in the same kinds of
destination as **Create schema**. **Create schema** can add them at the same
time with **Also create version tables**. The table has the same fields as
the live layer, and these extra columns:

| Column | What it stores |
|---|---|
| `ArchiveID` | Id of this version row |
| `Version` | 1 for the first save of that feature, then 2, 3, and so on |
| `ChangeType` | `insert`, `update`, or `delete` |
| `ChangeDate` | When the live layer was saved |
| `ChangedBy` | Database connection login, or the QGIS user when the layer is a file |
| `FeatureKey` | NGUID or StreetID of that version |
| `SourceFid` | Feature id on the live layer at that save |
| `ChangedFields` | On an update, the fields that changed. Includes `geometry` when the shape changed |
| `OldValues` | Previous values of those fields |
| `NewValues` | New values of those fields |

The other columns are a copy of the live feature as of that version. The
row's geometry is the shape after that save. The previous version row has
the earlier shape.

The table names end in `_Versions` (`RoadCenterline_Versions`, and the same
suffix on the street, address, and driveway tables). A table prefix is kept,
and the `_Versions` ending is kept even when the name has to be shortened to
63 characters. These columns belong to the plugin. They are not
NENA-STA-006 fields. A live field that already uses one of these names keeps
that live value.

**Track changes** (**Settings ▸ Project**) records each save of a centerline, street name, address
point, or driveway:

| Save | Version row |
|---|---|
| Insert | The feature that was added. This is version 1 when the feature is new |
| Update | The feature as it was saved, plus the old and new values |
| Delete | The feature that was removed |

Filter the version table on `FeatureKey` and sort by `Version` to read the
history of one road, street name, address, or driveway. The live layer keeps
the current feature. The version row is written when that layer is saved, and
the write is stored immediately. A version that cannot be written does not
undo the live save. Turn **Track changes** off to stop recording. The version
tables stay where they are.

A table created by an earlier build may be named `_Archive` and may have no
`Version`, `OldValues`, or `NewValues` columns. Tracking keeps writing to the
layer already chosen in the project, and adds those columns when the file or
database allows it. Rows saved before this change stored an update as the
feature before the edit, and their `Version` stays empty. Rows saved from
this version on are numbered versions of the feature after the save.

Boundary polygons are not part of the version tables.

---

## 3. Auto-ranging (panel ▸ *Ranges*)

**Ground rules**
* Digitized direction = increasing addresses. FROM is at the first vertex.
* Left/right are relative to the digitized direction. Default: **odd left,
  even right**; a valid existing `Parity_L/R` (e.g. `Z`) is kept unless you
  untick *Keep a valid existing Parity value*.
* **Scope = street chains**: connected segments with the same street key are
  ordered along the digitized direction (at a fork the straightest
  continuation is followed). Numbers are allocated sequentially, so
  consecutive segments **never overlap**, even very short ones.
  **Scope = each feature** ranges every feature on its own.

**Methods**

| Method | What it does | Key settings |
|---|---|---|
| Hundred-block | Segment *k* of a chain gets block *k*: 100–198 / 101–199, 200–298 / 201–299 … | Base address, block size, start numbering |
| Interval | Address grows by 1 number every *interval distance* along the chain | Distance per number + units (5.28 ft = 1000/mile) |
| Proportional | A total range is spread over the chain by length; optionally bent through address points (anchors) | Chain's existing min/max **or** fixed FROM/TO; *Bend through address points* |
| Address points | Nearest-line, side-aware assignment of house numbers; min/max per side, optionally widened to the block without touching neighbours | Point layer, number field, optional full-street-name field, search distance |

**Start numbering** (hundred-block & interval): *continue from upstream*
(the same-named segment that ends where the chain starts; next block or next
number), *keep existing FROM*, or *always base address*. Fallback order is
upstream → existing → base.

Click **Range selected** or **Range layer** on **Ranges ▸ Run**. One undo step
(“NENA: auto-range”) is added; the layer is put in edit mode if necessary.

**Fill range gaps** (**Ranges ▸ Run**) extends the upstream segment (or the selected downstream
segment) so its numbers meet the next segment of the same street. The gap
tolerance on **Check ▸ Checks** decides what counts as a gap. An overlap is
not closed.
Features whose values would not change are not touched; `DateUpdate` is
stamped only on changed features.

Lengths are measured on the ellipsoid, so any CRS works (geographic or
projected). Curves are segmentized and contiguous multipart lines merged
for endpoint/bearing calculations.

---

## 4. Re-ranging on split / merge / edit (panel ▸ *Settings, Editing* and *Ranges, Run*)

Tick **Re-range pieces after Split Features** (or use the toolbar toggle).
The setting is stored per layer. When you split a centerline with QGIS'
*Split Features* tool:

1. The plugin detects the split from the layer's edit command (geometry
   changed + features added).
2. Pieces are ordered along the original direction and the original range of
   each side is **redistributed proportionally to piece length** (first piece
   keeps FROM, last piece keeps TO, parity respected, decreasing ranges stay
   decreasing). With *Address points, proportional fallback*, sides with
   nearby points use the points.
3. New pieces get a fresh `RCL_NGUID` (if an agency id is set) and
   `DateUpdate` is stamped.
4. This is a **separate undo step**: *Undo* once restores the duplicated
   original values, twice undoes the split.

**Address points near the split (anchored re-ranging).** Plain proportional
splitting assumes numbers grow evenly along the line, which is often not
true: a house numbered 251 may sit 20 m into a segment whose range is
201–299, and a split at 25 m would hand 251 to the *second* piece even
though the house fronts the first. With the split method *Proportional,
anchored to address points* (default) and an address point layer set under
**Ranges ▸ Method**, the plugin

1. finds the address points that belong to the pieces (nearest centerline
   wins, among all centerlines nearby, matching the street name when a
   street field is set; side from the line direction),
2. drops points that can't be trusted (outside the original range, wrong
   parity for the side, or out of order along the line),
3. interpolates the address-vs-distance curve **through the remaining points**
   and clamps every split boundary between the last point before it and the
   first point after it.

Each point's number therefore always stays on the piece it lies along, and
the original FROM/TO are kept at the ends. Without points (or without a
point layer) it is plain proportional. The same anchoring is used by
*Redistribute ranges* and by the *Proportional* ranging method (option
*Bend through address points*).

**Split & Re-range map tool.** Toolbar ▸ *Split & Re-range* (or **Ranges ▸ Run ▸
Split & re-range**). Hover a centerline of the panel's layer:
the split position is previewed (your snapping settings apply). Click to
split: the line is cut with `curveSubstring` (curves, Z and M are kept),
both pieces are re-ranged with the split method above, the new piece gets a
new NGUID and the original's attributes (provider primary keys such as the
GeoPackage `fid` are left to the provider), and everything is **one undo
step** (“NENA: split & re-range”). Right-click or Esc leaves the tool. The
layer must be in edit mode. Clicks closer to an end than the connectivity
tolerance are refused.

**New road creates an intersection (on by default).** When you digitize a
new road (Add Line Feature) whose end lands on the middle of an existing
centerline — or that crosses one — the plugin:

1. splits the existing centerline at that point (several new roads or
   crossings on one line split it into several sections),
2. keeps the original FROM on the first section and the original TO on the
   last, and redistributes the range so **each new section continues the
   numbering of the previous one** (e.g. 201–299 / 200–298 split in the middle
   becomes 201–249 / 200–248 and 251–299 / 250–298), bent through address
   points when a point layer is set,
3. gives the new section(s) a fresh NGUID and stamps `DateUpdate`,
4. at crossings, also splits the new road so both end at the intersection.

This is one undo step (“NENA: split at new intersection”) right after the
add-feature step. Toggle it under **Settings ▸ Editing** (*Split existing roads where a new
road creates an intersection*); *…also where the new road crosses* can be
turned off separately for layers with overpasses/underpasses. A new road
that only extends an existing road from its end point splits nothing.
Works with or without topological editing. Batches of more than 50 added
features (paste/import) are not auto-split. Like every automatic edit
behaviour it is active once the layer's configuration is saved (**Save**,
or any ranging or validation run).

**Split policies.** With *Manage field split policies* on, the plugin sets the
QGIS field split policy of mapped range/name/parity fields to *Duplicate
value* and of `RCL_NGUID` to *Unset field*, so QGIS hands the original values
to every piece and never duplicates an NGUID.

**Merge.** With *Combine ranges after Merge* on, the merged feature gets
min(FROM)–max(TO) per side (parity kept if identical, otherwise `B`).
**Select the features before merging** — the plugin caches the selected
features' ranges so it still knows them after QGIS deletes the extras.

**Reshape / vertex edits.** Optionally recompute TO from the new length with
the interval settings (FROM kept), or re-derive from address points.

**Manual alternative.** **Redistribute selected** on **Ranges ▸ Run**
spreads the combined min/max of the selected chain proportionally. Use it
after splitting with any other tool, or after several edits.

**Roads that already cross.** **Split at intersections** on **Ranges ▸ Run** finds
places where one road ends on, or crosses, the interior of another, splits
there, and re-ranges. End-to-end joins are left alone. The selected roads
are processed, or the whole layer when nothing is selected. When the ZIP,
MSAG, and ESN polygons are set, the new sections are calculated again.
Crossings follow *…also where the new road crosses*.

**NGUID / agency** (**Settings ▸ Editing**). Enter the agency identifier (e.g. `psap.example.gov`) and
use *Fill empty* to populate empty `RCL_NGUID` values
(`urn:emergency:uid:gis:RCL:{uuid}:{agency}`, or the legacy
`RCL_{id}@{agency}`) and empty `DiscrpAgID`.

---

## 5. Validation (panel ▸ *Check*)

| Rule code | Severity | Meaning |
|---|---|---|
| `FIELD_NOT_MAPPED` | Error | A mandatory NENA field is not mapped/missing |
| `MANDATORY_EMPTY` | Error | Mandatory attribute is empty |
| `CONDITIONAL_EMPTY` | Info | `St_Name` empty |
| `PARITY_MISSING` / `PARITY_DOMAIN` | Error | Parity empty / not O,E,B,Z |
| `PARITY_NUMBER_MISMATCH` | Error | e.g. parity O with an even number |
| `PARITY_ZERO_MISMATCH` | Error/Warning | Z with numbers, or 0–0 without Z |
| `RANGE_MISSING` / `RANGE_NEGATIVE` / `RANGE_HALF_ZERO` | Error | Empty, negative, or one end 0 |
| `RANGE_REVERSED` | Warning | FROM > TO (unless *Accept decreasing ranges*) |
| `OVERLAP` | Error | Two segments of the same street (optionally + community) share a number, either side |
| `OVERLAP_SAME_SEGMENT` | Warning | Left and right of one segment share numbers (B/B) |
| `GAP` | Warning/Info | Numbers skipped between consecutive segments beyond the tolerance |
| `NOT_INCREASING` | Warning | Downstream segment starts at or below the upstream TO |
| `DIRECTION_CONFLICT` | Warning | Same-named segments meet head-to-head or tail-to-tail |
| `DIRECTIONAL_*`, `ONEWAY_DOMAIN`, `SPEED_*` | Error/Warning/Info | Domain problems (abbreviated directionals, bad OneWay…) |
| `NGUID_MISSING` / `NGUID_FORMAT` / `NGUID_LEGACY` / `NGUID_DUPLICATE` | Error/Warning/Info | NGUID problems |
| `GEOMETRY_*` | Error/Warning/Info | Empty, zero-length, disjoint multipart, invalid |
| `MSAG_MISSING` / `MSAG_COMMUNITY` / `MSAG_PARITY` / `MSAG_RANGE` | Error | That side of the centerline is not on the MSAG, or the community, odd/even, or low–high does not match |
| `MSAG_NOT_IN_GIS` / `MSAG_BAD_ROW` | Warning | An MSAG row matches no centerline, or its low/high is not a number |

**MSAG extract** (**Check ▸ MSAG**). Load the telephone company's file into the project, pick
the street, community, low, high, and odd/even fields, and **Compare
to MSAG**. `MAIN ST` matches `Main Street`. A centerline side
matches when its whole range sits inside a row of the same street, community,
and parity. Results use the same table as validation.

**Overlapping ranges.** With *Select overlapping ranges after a check*
(on by default) every feature involved in an `OVERLAP` or
`OVERLAP_SAME_SEGMENT` issue — both the reported feature and the one it
overlaps — is selected when validation finishes, ready for the attribute
table (*Show selected features*). *Find & select overlapping ranges* (also
on the toolbar) runs only the overlap check, which is much faster, and
selects the result; the *Select overlaps* button re-selects them from the
current results. *Select features with issues* now also includes the
related feature of overlaps and gaps.

*Validate selected* checks only the selection (relational checks still look
at the whole layer and report issues touching the selection). Double-click a
row to zoom; *Select features with issues*, *Export CSV…* and *Load as
layer* (a memory layer with a copy of each offending geometry) help review.

---

## 6. Recommended workflow

1. Load centerlines (+ address points if available). Make sure lines are
   **digitized in the direction of increasing addresses** and **split at
   intersections** (run **Check** → `DIRECTION_CONFLICT` helps; fix with
   *Vertex tool ▸ Reverse line* or *Processing ▸ Reverse line direction*).
2. **Settings ▸ Fields**: load your state's field template (if any), map / add fields →
   **Save**.
3. **Settings ▸ Editing**: enter your agency id, *Fill empty – all* NGUIDs.
4. **Ranges**: pick a method, range a test selection, inspect, then range the
   layer. Save the layer.
5. Set an address point layer under **Ranges ▸ Method** so splits are anchored to points.
   Turn on **auto re-range after split/merge**, or use the **Split &
   Re-range** tool, for day-to-day maintenance.
6. **Check** before every export to the NG9-1-1 GIS / SI; fix the selected
   overlapping features first.

---

## 7. Known limitations vs. full NENA compliance

* **Not a certification tool.** The plugin covers address ranges, parity,
  NGUIDs and a subset of attribute/geometry checks. It does not implement
  the complete NENA-STA-006 / NENA GIS Data Model validation, the
  NENA-STA-005 (Standards for the Provisioning and Maintenance of GIS data to
  ECRFs and LVFs) checks, or state profiles.
* **M/C/O flags and widths** follow NENA-STA-006.2 as commonly implemented;
  verify against the edition your authority uses (`core/schema.py`).
* **Street name domains** (`St_PreTyp`, `St_PosTyp` vs. the NENA/USPS
  registry), `RoadClass`, and `Country` / `State` / `County` code lists are
  not checked against an official registry. MSAG street, community, parity,
  and range are compared only when you run **Check ▸ MSAG**. Side-of-road
  mismatches are reported by the address check (`ATTRIB_MISMATCH`,
  `ATTRIB_NO_POLYGON`, `ATTRIB_MULTIPLE`).
* **Topology** is endpoint-based: lines are expected to be split at
  intersections. Interior crossings and dangles are not checked.
* **Chains follow the digitized direction**; a segment digitized backwards
  starts a new chain (and is flagged), it is not reversed automatically.
* **Split detection** relies on QGIS edit commands; external edits (e.g.
  another PostGIS client) are not seen. Re-ranging is a separate undo step.
* **Merge** needs the merged features to be selected beforehand (cache) or
  committed in the data source.
* **Anchored splitting** trusts the address points: a point assigned to the
  wrong side or segment (e.g. a corner lot addressed on the cross street
  without a street-name field to filter it) can still bend a boundary within
  the original range. Map the point layer's full street name field.
* **Auto-split at new intersections** cannot tell an at-grade crossing from an
  overpass – turn *…also where the new road crosses* off where that matters
  (T-intersections are still split). The new road itself is not ranged.
* The **Split & Re-range tool** splits one feature per click and works on the
  panel's layer only; topological editing is not applied to neighbours.
* **Addressing** measures the road start from the digitizing direction of the
  street's segments; a street digitized in mixed directions numbers from more
  than one start (validation flags the direction conflicts). Distance-based
  numbers use ellipsoidal lengths; the address point itself is not snapped
  to a building footprint layer.
* **Undo across layers** is per layer in QGIS; use *Undo last address* for the
  address + driveway pair.
* **Templates** describe fields only (names, types, widths, M/C/O); domains,
  defaults and constraints in a template GeoPackage are not imported.
* **Geographic CRS tolerances**: connectivity tolerance and address-point
  search distance are converted to degrees approximately (1° ≈ 111 km).
  Lengths are always ellipsoidal and exact.
* **Address number prefixes/suffixes** (`AdNumPre_*`, “123A”, hyphenated
  numbers) are not ranged; house numbers are parsed by their leading digits.
* Very large layers are processed on the main thread (progress bar shown).

## 8. Suggested next steps

* Processing provider exposing ranging/validation as algorithms (batch,
  models, headless QGIS).
* Building-footprint snapping for the rooftop click, and batch addressing of
  structures that have no address yet.
* Unit/sub-address tools (apartments, suites) on one address point.
* Street-name parsing / domain lists (NENA street type registry) and a
  “standardize names” tool.
* Intersection/topology checks (unsplit crossings, dangles, overshoots) and
  an interactive reverse-direction fixer.
* Split & Re-range tool: split at several clicked points in one go.
  **Split at intersections** on **Ranges ▸ Run** already does the batch pass.
* Export profile to the NENA GIS Data Model GeoPackage template.

---

## 9. Development

This repository is the plugin. The copy QGIS loads must be named `nena_rcl_tools`.

```
├── __init__.py            classFactory
├── metadata.txt           name, version, changelog
├── LICENSE                GPL-2.0-or-later
├── plugin.py              toolbar, menu, dock, watchers
├── compat.py              QGIS 3.34 through 4 (Qt5 and Qt6)
├── core/
│   ├── schema.py          NENA field list, domains, auto-mapping
│   ├── schema_pack.py     centerline, street, address, and driveway columns
│   ├── schema_writer.py   create those tables in a file or database
│   ├── config.py          per-layer settings
│   ├── project_config.py  project settings: layers, streets, addresses, MSAG, versions
│   ├── importer.py        copy existing layers into the schema
│   ├── ranging.py         parity and allocation
│   ├── ranger.py          auto-ranging engine
│   ├── topology.py        nodes, chains, direction
│   ├── gaps.py            close range gaps
│   ├── rules.py           validation rules
│   ├── validator.py       validation run, CSV, and review layer
│   ├── msag.py            compare centerlines to an MSAG extract
│   ├── defaults.py        fill empty fields
│   ├── nguid.py           NGUID builder
│   ├── geometry_utils.py  lengths, sides, CRS units
│   ├── layer_access.py    field access and undoable writes
│   ├── split_handler.py   split, merge, and reshape watcher
│   ├── split_ops.py       piece re-ranging and the split tool
│   ├── intersections.py   split where roads meet
│   ├── anchors.py         address points as range anchors
│   ├── template.py        field templates
│   ├── tables.py          create a table in a GeoPackage or database
│   ├── archive_model.py   version-row contents
│   ├── archive_watch.py   write a version after each save
│   ├── editor_user.py     database login into WhoEntered and LastUpdatedBy
│   ├── maintenance.py     NGUID fill and an empty template layer
│   ├── streets/           master names, sync, relations, PostgreSQL SQL
│   └── addressing/        numbering, address tool, sides, checks, reports
├── ui/                    panel, Settings pages, and the map tools (no .ui files)
├── icons/                 SVG icons, loaded by path
├── examples/              JSON configurations and field templates
├── docs/NENA_SCHEMA.md    field list, domains, and version columns
└── test/                  pytest, with test/fake_qgis when QGIS is not installed
```

`resources.qrc` is optional. The plugin does not need it compiled.

### Tests

No QGIS install is required. The engine tests use the small `qgis.core` stand-in in `test/fake_qgis`.

Most tests import the plugin as `nena_rcl_tools`. `test_archive.py` and `test_schema_pack.py` import it as `nena_addressing_manager`, and that import has to be the real directory. A Windows junction does not resolve `__file__`, so those two modules fail if the only copy on `PYTHONPATH` is a junction.

Name the real folder `nena_addressing_manager`. From its parent:

```powershell
New-Item -ItemType Directory -Force -Path "$env:TEMP\nena_py" | Out-Null
cmd /c mklink /J "$env:TEMP\nena_py\nena_rcl_tools" "$PWD\nena_addressing_manager"
$env:PYTHONPATH = "$env:TEMP\nena_py;$PWD"
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest nena_rcl_tools/test -q
cmd /c rmdir "$env:TEMP\nena_py\nena_rcl_tools"
```

Use `cmd /c rmdir` for the junction. Removing it in Explorer can delete the source folder.

License: GPL-2.0-or-later (required for the QGIS plugin repository).
