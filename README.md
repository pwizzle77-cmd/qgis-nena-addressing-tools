# NENA Addressing Tools

A QGIS plugin for day-to-day 911 addressing.

You keep one list of street names, give each road an address range, and click the map to place an address. The same panel can start a new set of tables, bring in data you already have, and check the result before you send it on.

It follows the usual NENA layout for road centerlines and address points. Your data can live in a GeoPackage, a file geodatabase, a shapefile, or a database you have already connected in QGIS.

You need QGIS 3.34 or newer, including QGIS 4. There is nothing else to install.

License: [GPL-2.0-or-later](LICENSE).
Source: https://github.com/pwizzle77-cmd/qgis-nena-addressing-tools

This guide matches version 3.2.10. The plugin was formerly called NENA RCL Tools. QGIS still loads it from a folder named `nena_rcl_tools`, so an existing install keeps its settings.

## Install

1. Copy this folder and name the copy `nena_rcl_tools`. The folder must contain `metadata.txt`.
2. Put that copy in your QGIS plugins folder.

| QGIS | Windows | macOS | Linux |
|---|---|---|---|
| 4 | `%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins\nena_rcl_tools` | `~/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins/nena_rcl_tools` | `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/nena_rcl_tools` |
| 3.34 or newer | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\nena_rcl_tools` | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/nena_rcl_tools` | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/nena_rcl_tools` |

3. Restart QGIS, or use Plugin Reloader.
4. In **Plugins → Manage and Install Plugins → Installed**, turn on **NENA Addressing Tools**.

To install from a zip, the zip must contain `nena_rcl_tools/metadata.txt` at the top. Use **Plugins → Manage and Install Plugins → Install from ZIP**.

You then get a **NENA Addressing Tools** toolbar and a **Vector → NENA Addressing Tools** menu.

| Button | What it does |
|---|---|
| 911 badge | Open or hide the panel |
| Street sign | Open the Streets tab |
| House and road | Place an address |
| Orange path | Draw a driveway |
| Three houses | Place several units that share one access |
| House with an undo arrow | Undo the last address |
| 101 → 199 | Give the selected roads an address range |
| Arc arrow | Spread one combined range across the selected pieces |
| Red split mark | Split a road and divide its range in one click |
| Scissors | Turn automatic re-ranging after a split or merge on or off |
| Shield | Check the whole layer |
| Overlapping bars | Select every road whose range overlaps another |

**Save** at the top of the panel stores these settings. It does not save edits on the layers. Save the layers with QGIS when you want the edits kept. Every edit the plugin makes can be undone until you save the layer.

## The panel

| Tab | Use it to |
|---|---|
| **Streets** | Keep the master list of road names, and see which roads and addresses use each name |
| **Addresses** | Place an address, draw a driveway, or number several units from one access |
| **Ranges** | Give roads their From–To numbers, close gaps, and split them |
| **Check** | Look for gaps, overlaps, and differences from an MSAG |
| **Settings** | Choose layers, match fields, and set the rules |

**Ranges** has two inner pages: **Run** does the work, **Method** chooses how the numbers are assigned. **Check** opens on **Results**. The other Check pages are **Checks** (what to look for) and **MSAG**.

Settings is split so each job has its own page:

| Page | What you set |
|---|---|
| **Project** | Create a new set of tables, import existing data, and keep a history of saves |
| **Fields** | Match your road layer's columns to the NENA names |
| **Sides** | Which polygon layers supply ZIP, community, and ESN |
| **Defaults** | State, county, one-way, and other values that rarely change |
| **Street rules** | When two names count as the same name, and what a rename does |
| **Address rules** | How a new address gets its number |
| **Editing** | What happens when you split, merge, or add a road |

Pick the road-centerline layer at the top of the panel. The panel also follows the line layer you have active in the Layers panel.

## A simple way to begin

1. Add your roads to the map. Draw each road in the direction the numbers increase. Split roads where they meet, or let the plugin split them later.
2. On **Settings → Fields**, confirm the plugin found the From, To, and street-name columns. Load your state's field list if you have one, then click **Save**.
3. On **Streets**, choose or create the master street-name table. **Build from roads** collects the names already on the roads and lets you pick one spelling for each road.
4. On **Ranges → Method**, choose how ranges are assigned. Try it on a few selected roads, look at the result, then range the rest.
5. On **Addresses**, choose the address-point layer. Click **Place address**, click the road where the driveway meets it, then click the building.
6. On **Check**, run a check before you export. Overlapping ranges are selected so you can fix them first.

## Street names

One table holds every road name. Roads and address points keep a link to that table, and they also keep the name parts an export needs (direction, name, type, and so on). When you rename a street, every linked road and address follows.

### Set up the list

| Setting | What to choose |
|---|---|
| Master street names | An existing table, or **Create** to make a new one in a GeoPackage or in any database connection QGIS already has |
| Address points | Optional until you are placing addresses. Renames update them too |
| Link field | `StreetID` unless your data already uses another column. **Add to layers** creates it |

**Add missing fields** fills in columns the master table does not have yet.

Each master row has the name parts, a full name, a status (Active, Proposed, or Retired), and room for the old name when a road is renamed.

### When two names are the same road

These choices are on **Settings → Street rules**.

| Choice | What it means | Usual setting |
|---|---|---|
| Where names must be unique | Across the whole county, or only inside one community, city, ZIP, or ESN | Whole county |
| What is compared | The full name, so North Main Street and South Main Street are different. Or ignore the street type, so Oak Street and Oak Avenue are the same | Full name |
| Sound-alike names | Smith Road and Smyth Road are reported | Warn |
| Exact duplicates | The plugin can refuse the save, or only warn you | Block |

`N` and `North` match. `St` and `Street` match. `Co Rd` and `County Road` match. Capital letters and periods are ignored.

**Street types** on that same page is the list in the name form. It starts as the usual USPS and NENA types: Street, Avenue, Lane, County Road, and the rest. You can add a local type or a local abbreviation. Click **Save** at the top of the panel to keep the list in the project.

Typing `ST` in the name form becomes `Street` before the name is checked and saved. A type already stored on a road is left as it is until you rename that road. A one-off type you type into a single name is not added to the list.

### Build the list from the roads

**Build from roads** reads the names on the centerlines and groups different spellings of the same road (`Main St`, `MAIN ST`, `Main Street`). Double-click the spelling you want to keep. Sound-alike names are listed so you can see them.

Build creates one master row per road. Run it again later and it reuses rows that are already there. It writes the link onto the roads, and onto address points with the same name. You can also have it rewrite those features to the spelling you kept. A road with a blank name is skipped.

### Day to day

- **Add** and **Rename** open a form with the name parts, a preview of the full name, and a check for duplicates. Rename tells you how many roads and addresses will change, and can keep the old spelling as a retired alias.
- **Retire** keeps the row for history. A check will flag anything that still uses it.
- **Assign to selected** links the selected road pieces to the street you picked.
- **StreetID drop-down** turns the link into a searchable list of active names in the attribute form.
- **Sync to features** copies the master name onto every linked road and address.
- **Check names** lists duplicates, sound-alikes, missing links, and names that have drifted out of sync.

Two automatic behaviors live under **When names change** on **Settings → Street rules**. You can turn either one off.

- If you type a new name part directly in the master table, linked features update.
- If you pick a street on a road or an address, the name parts are copied onto that feature.

Each of those is its own undo step.

**Database SQL** on the Streets tab is for PostgreSQL. It can write database rules so every program, not only QGIS, keeps the street links: a unique id, links that must match a real street, and a rename that updates the related rows. With a GeoPackage, the plugin keeps those links itself.

## Addresses

Choose the address-point layer on the Addresses tab. A driveway layer is optional. **Create** can make either layer in a GeoPackage or in a database connection.

A new address layer starts with the usual NENA site and structure fields. **Template** can load your state's field list instead. The plugin also keeps a few of its own columns on the point: which street it belongs to, which road it was measured on, where access meets the road, and which side of the road it is on. Leave those out of a NENA export if your exchange does not expect them.

The full road name is filled in when you create, re-address, assign, or sync an address. A full name you typed yourself stays until a rename of the master street replaces it.

You do not have to draw a driveway. The address point remembers where access meets the road either way. Draw a line when the path matters, or when several addresses share one driveway.

### Place an address

1. Click the road where access meets it. Snapping follows your QGIS snap settings. Click an existing driveway to use that driveway's access point instead.
2. Click the building.
3. The dialog shows the number, the street name, the community values, and any warning. You can change the number, suffix, unit, and placement before you create the point.
4. A straight driveway is not drawn unless you hold **Shift** on the building click, or you turned **Straight driveway with Place address** on. You can also tick **Draw a straight driveway** in the dialog.
5. Backspace or Esc clears the click. Esc again, or a right-click before you have clicked, leaves the tool.

**Undo last address** removes that point and, if the same click drew a straight driveway, the driveway too.

### Draw a driveway

1. Click the road, or click an existing driveway to start a branch off it.
2. Click each bend. The line follows the cursor.
3. Right-click or double-click to finish. Backspace removes the last bend. Esc starts over.

The line is not tied to an address yet. The next **Place address** or **Place units** click on that line uses its access point, so several addresses can share it. Undo the driveway on the driveway layer.

### Place units

Use this for an apartment building, a townhouse row, or any site where many addresses share one access.

1. Click the road or an existing driveway once. A small panel shows the base number, the numbering style, and the next unit.
2. Click each building. Each click creates an address with that same access. The tool stays on.
3. Right-click or Esc ends this access. Esc again leaves the tool.

| Style | What you get |
|---|---|
| 101, 102, 103 | The street number steps by one |
| 101A, 101B | One street number, with a letter suffix. Start at 1 for A |
| 1, 2, 3 | The unit field. Leave the prefix blank, or type `Unit ` to get Unit 1, Unit 2 |

The first building fills in the base number. You can change it before the next click. **Straight driveway for each unit** is off. Turn it on only when each unit needs its own line from the access point to that building.

### How the number is chosen

**Settings → Address rules** controls this. The first unit in a Place units session uses the same rules.

| Step | What happens |
|---|---|
| Side of the road | The building's side, judged from the direction of the road at the access point. This still works on a curve or a corner lot |
| The number | **Along the road** (the usual setting): start at the low end of the street and add one number every so often. 5.28 feet is about 1,000 numbers per mile. **Inside the road's range**: the From and To already on that side of the segment decide the number. **By frontage**: a fixed count of numbers for each lot width |
| Odd or even | Rounded to the side's parity. The road's own left/right parity wins when it is filled in. A side marked Z has no addresses, and the tool will not place one there |
| Order | The new number stays between the addresses already on that side, so numbers still increase along the street |
| A number already used | Take the next free number, add a suffix such as 101A, or ask. A suffix is used when no whole number fits between the neighbors |
| One community, different rules | A community, ESN, ZIP, city, or county can have its own method, spacing, or starting number |

The road's start is the start of that street's connected pieces, in the direction they were drawn. A branch continues from the point where it leaves the main road.

If the calculated number falls outside the From–To on that side, the dialog says so and suggests re-ranging the segment.

### Which community, ZIP, and ESN

On **Settings → Sides**, pick the polygon layer and the field for ZIP, MSAG community, and ESN. A road is often the boundary, so the left side and the right side can differ.

**Calculate sides** writes those values onto the road, a few metres off each side. **Split where the side changes** cuts the road where the polygon changes, gives each piece its own left and right values, and updates the addresses that sit on those pieces.

A new address copies the side it sits on. **Copy road side** does the same for points you have already placed. An empty side on the road is filled in when the address is created, then copied to the point.

While address points are selected, the map shows a temporary marker: a circle where access meets the road, a dashed line to the building, and the number. Blue means the access point is still on the road. Red means the road has moved away from the stored access point. A hollow grey circle means the access point was inferred. Clear the selection and the markers go away. Nothing is written.

### Renaming part of a road

If you assign some pieces of a road to a different street, and those pieces already have addresses, the addresses take the new name. A dialog lists them with the numbers the new street would give. You can keep the old numbers, re-address all of them, or re-address only the ones you tick.

### Reports

**Streets report** is a CSV of every master name, with its status, alias, how many pieces and addresses it has, and the lowest and highest number. **Addresses by community** counts addresses by MSAG, ZIP, ESN, or city.

## Address ranges

Open **Ranges**. The method you pick is shown on the Run page.

A few rules apply to every method:

- Draw the road in the direction the numbers increase. From is at the start of the line.
- Left and right are from that direction. The usual pairing is odd on the left and even on the right. An existing parity, including Z for "no addresses on this side", is kept unless you turn that off.
- **Street chains** treats connected pieces of the same road as one street and numbers them in order, so neighboring pieces do not overlap. **Each feature** numbers every piece on its own.

| Method | What you get |
|---|---|
| Hundred-block | Each piece of the street gets the next block, such as 100–198 and 101–199, then 200–298 and 201–299. You set the starting number and the block size |
| Interval | The number grows by one for every distance you set. 5.28 feet is about 1,000 numbers per mile. Any coordinate system works, because length is measured on the ground |
| Proportional | You supply the low and high number for the whole street, or the plugin uses the low and high already on the pieces. The range is spread by length. It can bend through existing address points so a house keeps a range that contains its number |
| From address points | The range on each side comes from the house numbers near that piece. You can widen the result out to the whole block |

For hundred-block and interval, the street can continue from the piece upstream, keep the From already on the first piece, or always start at the base number you typed.

**Range selected** and **Range layer** do the work in one undo step. The layer is put in edit mode if it was not already.

**Fill range gaps** extends a piece so its numbers meet the next piece of the same street. What counts as a gap is the tolerance on **Check → Checks**. An overlap is left alone.

Only features whose values change are edited.

## When you edit a road

These options are on **Settings → Editing**. The split tool and the gap tool are on **Ranges → Run**.

**Re-range pieces after Split Features.** After you split a road with QGIS, the original range is divided across the pieces by length. The first piece keeps From, the last piece keeps To, and odd and even stay as they were. Undo once puts the original numbers back. Undo again removes the split.

If an address-point layer is set on **Ranges → Method**, the split is anchored to those points. A house keeps its number on the piece it actually fronts, even when the numbers are not spaced evenly along the road. Points that fall outside the range, on the wrong parity, or out of order are ignored. With no points, the split is by length only.

**Split & Re-range** is the one-click tool. Hover to preview the cut, then click. Both pieces are re-ranged, and the new piece gets a new id. It is one undo step. Right-click or Esc leaves the tool. The layer has to be editable. A click too close to an end is ignored.

**A new road that meets an existing road.** This is on by default. When you draw a road that ends on the middle of another road, or crosses one, the existing road is split there and the range continues on the new piece. A road that only extends another road from its endpoint does not cause a split. You can turn crossings off where you have overpasses. Pasting or importing a large batch is not split automatically. The new road itself is not given a range by this step.

**Roads that already cross.** **Split at intersections** finds those places in data you imported, splits them, and re-ranges. End-to-end joins are left as they are. If nothing is selected, the whole layer is checked. When ZIP, MSAG, and ESN polygons are set, the new pieces are calculated again.

**Merge.** Turn on **Combine ranges after Merge**, and select the pieces before you merge them. The merged road gets the lowest From and the highest To on each side.

**Reshape.** You can have the high number recomputed from the new length, or taken again from nearby address points. The low number stays.

**Redistribute selected** spreads the combined low and high of the selected pieces by length. Use it after you split with some other tool.

**Ids.** On **Settings → Editing**, enter your agency id. **Fill empty** writes a NENA id on roads that do not have one, and fills an empty discrepancy agency id. New pieces from a split get a new id so two roads never share one.

## Checking the data

**Check → Results** lists what it found. Double-click a row to zoom. You can select the features, export a CSV, or load a temporary layer of the problem shapes. **Validate selected** limits the list to the selection. Checks that compare one road with another still look at the whole layer.

Overlapping ranges are selected when the check finishes, so they are ready in the attribute table. The toolbar button **Find & select overlapping address ranges** runs only that check, which is faster on a large layer.

The check looks for:

- Required values that are missing, including a parity that is empty or not odd, even, both, or none
- A range that is missing, negative, only filled in on one end, or running backwards
- Two pieces of the same street that claim the same numbers, or a gap between pieces
- Pieces of one street that were drawn in conflicting directions
- A parity that does not match the numbers on that side
- Ids that are missing, duplicated, or not in the expected form
- Empty, zero-length, or broken geometry
- Names that no longer match the master list

**Check → MSAG** compares your roads with the telephone company's MSAG. Load that file into the project, then pick the street, community, low, high, and odd/even columns. `MAIN ST` matches `Main Street`. A side of a road matches when its whole range sits inside an MSAG row for the same street, community, and odd/even. Rows on the MSAG that are not in your map are listed too.

Address checks, from the Addresses tab, catch a number outside the road's range, the wrong odd/even side, a number out of order, a duplicate, an access point that has left the road, a missing street, and a ZIP, community, or ESN that does not match the side of the road.

## Settings, one page at a time

### Project

**Create schema** makes an empty set of tables and points the panel at them. **Import** copies features from layers already in the project. **Versions** creates the history tables. **Track changes** records each save. Details are in the next section.

### Fields

The plugin matches columns by NENA name and by names often seen in older data, such as `L_F_ADD`, `FROMLEFT`, and `STREETNAME`. Review the list. Required fields that are still unmatched are shown in bold.

**Add mandatory** and **Add all fields** create the missing columns. Names stay short enough for a shapefile.

**Street key** decides which pieces belong to the same street. The default is all of the name parts together.

**Field template** loads the list of columns your layer is supposed to have, from a GeoPackage, shapefile, GeoJSON, a layer already in the project, or a JSON or CSV file. Examples are in the `examples` folder. After it loads, the plugin matches those fields and can add any that your layer does not have yet. **Back to the built-in NENA schema** clears the template.

**Save** stores the field match on the layer, so it travels with the project. **Export JSON** and **Import JSON** reuse it in another project.

The field list, with the usual required and optional flags, is in [`docs/NENA_SCHEMA.md`](docs/NENA_SCHEMA.md).

### Sides

Pick the ZIP, MSAG community, and ESN polygon layers, then calculate or split as described under Addresses. Other polygon values can be added as extra rules if you need them.

### Defaults

State, county, country, one-way, parity, and any other field you add here are filled only where the feature is blank. A value someone already entered is left alone. A new road and a new piece from a split get the same fill.

Leave parity blank here if you want ranging to set odd and even.

A new address uses the default for its side when that side of the road is empty.

### Street rules and address rules

Covered above under Street names and Addresses.

### Editing

Covered above under When you edit a road. The automatic tools start working after the layer's settings have been saved.

## New tables, import, and history

### Create a fresh set of tables

**Settings → Project → Create schema**, or **Vector → NENA Addressing Tools → Create NENA schema**.

| Destination | What you pick |
|---|---|
| GeoPackage | One `.gpkg` file. The tables are added to it |
| File geodatabase | A folder whose name ends in `.gdb` |
| Database | A connection already defined in QGIS. The schema, such as `public` or `dbo`, is created if it is not there yet |

Choose one coordinate system. It is used for the roads, the address points, and the driveways. A table prefix is optional.

| Table | What it holds |
|---|---|
| Road centerlines | The road lines and their ranges |
| Street names | The master name list |
| Address points | One point per address |
| Driveways | The path from the road to the building, when you draw one |

**Replace tables that already exist** overwrites tables of those names. Other tables in the file or database are left alone. The new layers are grouped in the project.

ZIP, MSAG, and city polygons are not created here. Keep the polygon layers you already have, and point **Settings → Sides** at them.

### Import what you already have

**Import** copies features from layers in the project into those tables. Each destination has its own source. The column match starts from NENA names and older aliases. **Fields** lets you change the match before the copy.

You can import only the selected features. Geometry is reprojected into the destination. A street-name row with a blank street name is left out. A road or an address is still copied when its name is blank, and any required field that could not be matched is reported.

Import writes the destination immediately. Later edits with the address and range tools stay in the edit buffer until you save those layers.

### Who made the edit

On PostgreSQL, SQL Server, Oracle, and SAP HANA, a save writes the database login. **Who entered** is filled only when it is empty, and only on a new feature. **Last updated by** is written on every new or changed feature. A value already in **Who entered** stays, including on a piece created by a split.

A GeoPackage, file geodatabase, or shapefile has no database login, so those fields are left as they are.

### History of each save

**Versions** creates one history table for each layer you check, in the same kinds of places as **Create schema**. **Create schema** can add them at the same time.

Turn on **Track changes**. Each time you save a road, a street name, an address, or a driveway, a row is added:

| What you saved | What is recorded |
|---|---|
| A new feature | That feature, as version 1 |
| A change | The feature as it was saved, plus the old and new values of the fields that changed. A shape change is included |
| A delete | The feature that was removed |

The history row stores the shape after that save. The previous row still has the earlier shape. The live layer keeps the current feature.

Filter the history table on the feature's id and sort by version to read one road, name, address, or driveway. The history is written when the live layer is saved, and it is stored immediately. If a history row cannot be written, the live save still stands. Turn **Track changes** off to stop recording. The history tables stay where they are.

An older history table may be named with `_Archive`. Tracking keeps writing to the table the project already uses.

Boundary polygons are not part of this history.

## Good to know

- This is an editing tool, not a certification against the full NENA standard. Check your authority's own profile before an official submission.
- Roads are expected to meet at their ends. A crossing in the middle of a line is not a clean intersection until it is split. The plugin can do that split for you.
- A piece drawn backwards starts its own numbering. The check flags those direction conflicts. It does not reverse the line for you.
- The plugin sees edits made in this QGIS session. An edit made by another database client is not re-ranged automatically.
- Select the pieces before you merge them, so the plugin still knows their ranges after QGIS deletes the extras.
- A new crossing and an overpass look the same to the automatic split. Turn off splits-on-crossings where you have grade separations. A T intersection is still split.
- Undo is kept per layer. **Undo last address** is the way to remove an address and the driveway that was created with it.
- A field template brings names, types, and whether a field is required. It does not bring domains or default values stored in a template file.
- Very large layers are processed in QGIS, with a progress bar.
- House-number suffixes and hyphenated numbers are not used when ranging. The number is read from the leading digits.
- Not in this version: a Processing toolbox algorithm, snapping the building click to a footprint layer, rewriting street types already stored on features, or a topology check for dangles and overshoots.

## Recent changes

- **3.2.10** — This guide. The tools and the settings are explained in plain language.
- **3.2.9** — Place address, Draw driveway, and Place units are separate tools. A driveway is drawn only when you ask for one. Place units numbers several buildings from one access. The street-name form has an editable list of street types.
- **3.2.8** — The panel is Streets, Addresses, Ranges, Check, and Settings. Everyday work stays on the first four. Setup is under Settings.
- **3.2.7** — On a database, Who entered and Last updated by use the connection's login.
- **3.2.6** — Split a road where the community side changes, split roads that already cross, close gaps in a range, compare roads with an MSAG, and fill empty fields from defaults.
- **3.2.5** — ZIP, MSAG, and ESN come from the polygon beside each side of the road. A new address copies the side it sits on.
- **3.2.4** — Each save can record a numbered version, with the old and new values.
- **3.2.3** — Building the street list skips a road with no name. Side values are taken from the polygon beside that side.
- **3.2.2** — After a split, the original range is divided across the pieces. New addresses get the full road name.
- **3.2.1** — The panel scrolls, and the toolbar has its own row.
- **3.2.0** — Optional history tables for roads, street names, addresses, and driveways.
- **3.1.0** — Create a new set of tables, and import roads, names, addresses, and driveways into them.
- **3.0.0** — Click-to-address, with driveways, community values, and address checks.
- **2.0.0** — Master street names, with rename across every linked feature.
- **1.0.0** — Field mapping, address ranges, and validation.

Older notes are in the plugin's About box.

## If you change the plugin

This repository is the plugin. The copy QGIS loads has to be named `nena_rcl_tools`.

Tests do not need a QGIS install. Most of them import the plugin as `nena_rcl_tools`. Two test modules import the real folder by its source name, so a Windows junction alone is not enough. From the parent of the real folder:

```powershell
New-Item -ItemType Directory -Force -Path "$env:TEMP\nena_py" | Out-Null
cmd /c mklink /J "$env:TEMP\nena_py\nena_rcl_tools" "$PWD\nena_addressing_manager"
$env:PYTHONPATH = "$env:TEMP\nena_py;$PWD"
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest nena_rcl_tools/test -q
cmd /c rmdir "$env:TEMP\nena_py\nena_rcl_tools"
```

Remove the junction with `cmd /c rmdir`. Deleting it in Explorer can delete the source folder.

The field list is in [`docs/NENA_SCHEMA.md`](docs/NENA_SCHEMA.md).
