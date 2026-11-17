# NENA Addressing Tools for QGIS

**A QGIS plugin that handles the day-to-day work of 911 addressing.**

Keep one clean list of street names, give your road centerlines their address ranges, and place new addresses by clicking on the map. The plugin also checks your data before you send it to your 911 authority or state.

It follows the NENA NG9-1-1 layout for road centerlines and site/structure address points. Your data can be in a GeoPackage, a file geodatabase, a shapefile, or any database you've connected in QGIS (PostgreSQL, SQL Server, and others).

| | |
|---|---|
| **Works with** | QGIS 3.34 or newer, including QGIS 4 |
| **Extra software** | None |
| **Version** | 3.6.3 |
| **License** | [GPL-2.0-or-later](LICENSE) |
| **Source** | https://github.com/pwizzle77-cmd/qgis-nena-addressing-tools |

---

## Contents

- [What can it do?](#what-can-it-do)
- [Install](#install)
- [Get started in 10 minutes](#get-started-in-10-minutes)
- [The toolbar and windows](#the-toolbar-and-windows)
- [Map styles and line direction](#map-styles-and-line-direction)
- [Drop-down lists (lookup tables)](#drop-down-lists-lookup-tables)
- [Street names](#street-names)
- [Placing addresses](#placing-addresses)
- [Address ranges on roads](#address-ranges-on-roads)
- [When you edit a road](#when-you-edit-a-road)
- [Checking your data](#checking-your-data)
- [Settings](#settings)
- [New tables, importing, and edit history](#new-tables-importing-and-edit-history)
- [Good to know](#good-to-know)
- [Words used in this guide](#words-used-in-this-guide)
- [What's new](#whats-new)
- [For developers](#for-developers)

---

## What can it do?

| You want to… | The plugin… |
|---|---|
| Stop misspelled and duplicate street names | Keeps one master street-name list. Rename a street once and every road and address using it updates. |
| Range your centerlines | Assigns From/To numbers by hundred-block, distance, length, or from existing address points. |
| Add new addresses quickly | You click the road, then the building. The house number, side, ZIP, ESN, and community fill in for you. |
| Number apartments and townhomes | Places many units under one house number: 1, 2, 3 or 101, 102 or A, B, C. |
| Keep attribute values consistent | Gives fields drop-down lists, so everyone picks *Main Street*, not *Main St.* or *MAIN*. |
| Split roads without breaking ranges | Divides the range across the pieces automatically when you split. |
| See which way a road runs | Styles roads with direction arrows, a green dot where each line starts, and a red square where it ends. |
| Fix a road drawn backwards | **Reverse** flips the line and swaps its left/right values and ranges to match. |
| Catch problems before submitting | Finds overlapping ranges, gaps, wrong odd/even sides, missing values, and MSAG mismatches. |
| Start fresh or move old data in | Creates empty NENA tables and imports your existing layers into them. |

---

## Install

1. Copy the plugin folder and name the copy **`nena_rcl_tools`**. The folder must contain `metadata.txt`.
2. Put the copy in your QGIS plugins folder:

| QGIS | Windows | macOS | Linux |
|---|---|---|---|
| 4 | `%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins\nena_rcl_tools` | `~/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins/nena_rcl_tools` | `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/nena_rcl_tools` |
| 3.34+ | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\nena_rcl_tools` | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/nena_rcl_tools` | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/nena_rcl_tools` |

3. Restart QGIS.
4. Go to **Plugins → Manage and Install Plugins → Installed** and tick **NENA Addressing Tools**.

**Installing from a zip?** The zip must have `nena_rcl_tools/metadata.txt` at the top. Use **Plugins → Manage and Install Plugins → Install from ZIP**.

When it's on, you'll see the **NENA Addressing Tools** toolbar and a **Vector → NENA Addressing Tools** menu.

---

## Get started in 10 minutes

This is the usual order for a county that already has road centerlines.

1. **Add your roads to the map.**
   Each road line should point the way the house numbers go up. Roads should be split where they meet (the plugin can do this for you later).

2. **Style the layers.** Click **NENA ▾ → Style NENA layers**.
   Roads now look like roads, with arrows showing which way each one runs. Look for roads whose arrows point the wrong way, select them, and click **Reverse**.

3. **Match your fields.** Open **NENA ▾ → Settings → Fields**.
   The plugin matches your columns to NENA names on its own, including older names like `L_F_ADD` and `STREETNAME`. Check the list, and fix anything it missed.

4. **Set up the drop-down lists.** Open **NENA ▾ → Lookup tables**.
   Click **Create missing tables**, then **Load values from my data**. Your attribute forms now have drop-downs for street type, county, ZIP, ESN, community, and more.

5. **Build your street-name list.** Open **NENA ▾ → Streets**.
   Pick or create a master street-name table, then click **Build from roads**. Choose one spelling for each street.

6. **Range the roads.** Open **NENA ▾ → Ranges**.
   Pick a method on the **Method** page. Try it on a few selected roads first, check the result, then range the rest.

7. **Place an address.** Click **Address** on the toolbar.
   Click the road, then click the building. An access point is saved 10 feet off the road.

8. **Check before you submit.** Click **Validate** on the toolbar.
   Overlapping ranges are selected for you so you can fix them first.

> 💡 **Your settings save automatically.** Your layer edits don't. Save your layers in QGIS when you want to keep the edits. Until then, everything the plugin did can be undone.

---

## The toolbar and windows

### Toolbar

Every button has its name under the icon.

| Button | What it does |
|---|---|
| **NENA ▾** | Opens a window (Streets, Addresses, Ranges, Check, Lookup tables, Schema and import, Settings), or **Style NENA layers** |
| **Split** | Click a road to split it and divide its range between the two pieces |
| **Auto re-range** | Turns automatic re-ranging on or off after you split or merge roads |
| **Range** | Gives the selected roads an address range |
| **Redistribute** | Spreads one combined range across the selected pieces |
| **Reverse** | Flips the direction of the selected roads. Left/right values and ranges follow |
| **One-way ▾** | Marks the selected roads two-way (B), one-way From → To (FT), or one-way To → From (TF) for routing |
| **Validate** | Checks the whole centerline layer |
| **Overlaps** | Selects every road whose range overlaps another |
| **Address** | Places an address and an access point 10 feet off the road |
| **Driveway** | Draws a driveway from the road |
| **Units** | Places several units that share one house number |
| **Undo** | Removes the last address you placed |

### Windows

| Window | Use it to |
|---|---|
| **Streets** | Manage the master street-name list, and see which roads and addresses use each name |
| **Addresses** | Choose your address, driveway, and access-point layers |
| **Ranges** | Assign From/To numbers, fill gaps, and split roads |
| **Check** | Find gaps, overlaps, and MSAG differences |
| **Lookup tables** | Manage the drop-down lists |
| **Schema and import** | Create new NENA tables, import old data, and track edit history |
| **Settings** | Match fields and set the rules (things you set once) |

Windows remember their size and position.

**Rather keep them docked?** Tick **NENA ▾ → Dock the NENA windows in the side panel**. They'll open as tabs next to the Browser/Catalog instead of as separate windows.

**Choosing the centerline layer:** each window has a **Centerline** picker at the top. The windows also follow whichever line layer is active in the Layers panel.

---

## Map styles and line direction

### Style your layers

Click **NENA ▾ → Style NENA layers**. New tables made with **Create schema** are styled for you.

| Layer | How it looks |
|---|---|
| **Road centerlines** | Grey-edged white roads. Blue **arrows** show the direction the line was drawn; on one-way roads, large **red arrows** show the direction of travel instead. A **green dot** marks the first point (From) and a **red square** the last point (To). Zoomed in, the street name runs along the road and the From/To numbers for each side appear at each end |
| **Address points** | Small orange houses, labelled with the house number, suffix, and unit when you zoom in |
| **Driveways** | Brown dashed lines |
| **Access points** | Small blue diamonds |

**Your old style isn't lost.** The plugin adds a style called **NENA** to each layer. To switch back, right-click the layer in the Layers panel → **Styles** → pick the original (usually *default*).

### Why direction matters

House numbers follow the direction a road was drawn:

- **From** is at the start of the line (green dot), **To** is at the end (red square).
- **Left** and **Right** are as you'd see them driving from the green dot toward the red square.
- New addresses are numbered by their distance from the start of the street, so a road drawn backwards gets its numbers backwards too.

If addresses are coming out wrong on a road, look at its arrows. They should point the way the numbers go up.

> 💡 **Check → Results** also lists pieces of a street that run against their neighbors (*DIRECTION_CONFLICT*). Select them from the results, then click **Reverse**.

### One-way roads (routing)

The NENA **OneWay** field tells routing which way traffic may travel:

| Value | Meaning |
|---|---|
| **B** | Two-way |
| **FT** | One-way **From → To**: traffic travels the way the line was drawn (green dot toward red square) |
| **TF** | One-way **To → From**: traffic travels against the way the line was drawn |

Select the roads, click **One-way ▾** on the toolbar, and pick **Two-way**, **From → To**, **To → From**, or **Clear**. Only OneWay changes; the line and its address numbers stay as they are. If the layer has no OneWay field yet, the plugin offers to add it.

With the NENA style, one-way roads show **large red arrows in the direction of travel** in place of the small blue drawing-direction arrows, so you can check them at a glance.

> 💡 **Reverse** flips FT ↔ TF for you, so the direction of travel stays the same on the ground.

### Reverse a road

1. Select the road pieces that point the wrong way.
2. Click **Reverse** on the toolbar.
3. Choose one:

| Choice | Use it when |
|---|---|
| **Reverse and re-range** (usual) | The line was drawn backwards. The plugin reverses it, then ranges it again in the new direction with your parity settings (odd on the left, even on the right) |
| **Reverse only** | You want to keep the existing numbers exactly. Each side's From/To and parity move with it, so they now read the other way |

What happens either way:

- The line is flipped, so the green dot and red square swap ends.
- All left/right fields swap (ESN, ZIP, community, parity, and the rest), so values stay on the correct physical side of the road.
- With **Reverse only**, From/To ranges and parity swap so every house keeps its number. With **Reverse and re-range**, the roads get new ranges with odd on the left and even on the right.
- One-way flips (**FT** ↔ **TF**).
- Address points and access points on that road switch side (**L** ↔ **R**) to match.

It's one undo step, like any other edit. Save the layer to keep it.

> ⚠️ QGIS's own **Reverse Line** tool flips the line only. Its left/right fields and ranges stay where they were, so they end up on the wrong side. Use the plugin's **Reverse** instead.

---

## Drop-down lists (lookup tables)

Drop-downs make sure everyone types values the same way. Instead of *St*, *ST.*, and *Street* all ending up in your data, the editor picks from a list.

### Which fields get a drop-down

| Kind of list | Lists | Where they're stored |
|---|---|---|
| **Lookup tables** | Road (street) type, unit type, address status, road status, address type | One small table each |
| **Local lists** | County, incorporated municipality, unincorporated community, neighborhood, ZIP code, postal community, ESN, MSAG community, road class, placement method, discrepancy agency, street-name separator | One shared table called **NENA_Domains** |
| **Fixed NENA lists** | Directionals (North, South…), parity (O/E/B/Z), one-way (B/FT/TF), valid (Y/N), country, state | Built in. Nothing to set up |

These go on your **address points**, **both sides of your centerlines** (`_L` and `_R` fields), and your **street-name table**.

**ZIP narrows the postal community.** Each postal community can list its ZIP code as its **Parent**. Once a feature has a ZIP code, the postal-community drop-down shows only the communities in that ZIP.

### Setting them up

Open **NENA ▾ → Lookup tables**. The top of the window lists every drop-down, where it's stored, and how many fields use it. Lists shown in red are missing.

1. **Create missing tables.** Makes any missing table in the same place as your address points (same GeoPackage, file geodatabase, or database) and fills it with standard values.
   - Tables that already exist are reconnected, not overwritten.
   - Using shapefiles? A new `NENA_Lookups.gpkg` is created next to them.
2. **Load values from my data.** Gathers the counties, communities, ZIP codes, ESNs, and other values already in your address points and centerlines. You'll see the list first, so you can untick any typos before they're added.
3. **Reconnect drop-downs.** Puts the drop-downs back on every matching field. Use this after adding new fields or layers.
   - Fields are found by your field match, by NENA name, or by common older names like `PRETYPE` and `ZIP_L`.
   - If you set up a different widget on a field yourself, it's left alone unless you choose to replace it.

Drop-downs are reapplied every time you open the project.

### Editing a list

Click a list at the top, then edit its values below. **Every change saves right away.**

| To… | Do this |
|---|---|
| Add a value | Type the **Code** (what's stored) and **Label** (what's shown), then click **Add** |
| Change a value | Double-click the cell |
| Stop offering a value | Untick **In list**. It stays in the table but drops out of the drop-down |
| Reorder | Drag rows, or use **Move up**, **Move down**, or **Sort A–Z** |
| Remove a mistake | Select it and click **Delete** |

> ⚠️ Features store the **code**. If you change a code, features that already use the old code are not updated.

**How the stored value works:** address points store the code for unit type, status, and address type. Roads store the road-status code. Street types store the full word (*Street*, *Lane*, *Court*) so the full road name reads naturally. The Road Type list still keeps the abbreviation (*ST*, *LN*) as its code.

---

## Street names

The plugin keeps **one master table of street names**. Roads and address points link to it, and they also keep their own copy of the name parts (directional, name, type, and so on) for NENA exports.

**Rename a street once, and every linked road and address updates.**

### Set up the list

Open **NENA ▾ → Streets**.

| Setting | What to choose |
|---|---|
| Master street names | An existing table, or **Create** to make one in a GeoPackage or database |
| Address points | Optional until you start placing addresses. Renames update them too |
| Link field | Usually `StreetID`. **Add to layers** creates it on your roads and addresses |

Each street row holds the name parts, the full name, a status (Active, Proposed, or Retired), and the old name after a rename.

### Build the list from your roads

**Build from roads** reads the names on your centerlines and groups different spellings of the same street together (`Main St`, `MAIN ST`, `Main Street`). Double-click the spelling you want to keep.

- One master row is created per street. Running it again reuses existing rows.
- The link is written to the roads, and to address points with the same name.
- You can also have it rewrite those features to use the spelling you picked.
- Roads with no name are skipped.

### Everyday tasks

| Button | What it does |
|---|---|
| **Add** / **Rename** | Opens a form with the name parts, a preview of the full name, and a duplicate check. Rename tells you how many roads and addresses will change, and can keep the old name as a retired alias |
| **Retire** | Keeps the street for history. The check flags anything still using it |
| **Assign to selected** | Links the selected road pieces to the chosen street |
| **StreetID drop-down** | Makes the link a searchable list of active street names in the attribute form |
| **Sync to features** | Copies the master name onto every linked road and address |
| **Check names** | Lists duplicates, sound-alikes, missing links, and names out of sync |

Two things happen automatically. You can turn either off under **Settings → Street rules → When names change**:

- Edit a name part directly in the master table, and the linked features update.
- Pick a street on a road or address, and its name parts are copied to that feature.

### When are two names "the same"?

Set this under **Settings → Street rules**.

| Choice | What it means | Usual setting |
|---|---|---|
| Where names must be unique | The whole county, or only within one community, city, ZIP, or ESN | Whole county |
| What gets compared | The full name (*N Main St* ≠ *S Main St*), or the name without its type (*Oak St* = *Oak Ave*) | Full name |
| Sound-alike names | *Smith Rd* vs *Smyth Rd* are reported | Warn |
| Exact duplicates | Refuse the save, or just warn | Block |

These always count as the same: `N` / `North`, `St` / `Street`, `Co Rd` / `County Road`. Capital letters and periods are ignored.

**Street types list.** **Settings → Street rules → Street types** is the list shown in the name form. It starts with the standard USPS and NENA types, and you can add local types or abbreviations. Typing `ST` becomes `Street` before the name is checked. Types already stored on roads stay as they are until you rename that road.

**PostgreSQL users:** **Database SQL** in the Streets window can add database rules so the street links stay correct even when someone edits outside QGIS. With a GeoPackage, the plugin handles this itself.

---

## Placing addresses

Open **NENA ▾ → Addresses** and choose your address-point layer and your access-point layer. A driveway layer is optional.

> **What's an access point?** It's the spot just off the road where the address is reached. Place address saves one 10 feet straight off your click, on the building's side of the road. The address stores that access point's id, and the access point stores the road's id. Several addresses can share one, like an apartment building.

A new address layer starts with the standard NENA site/structure fields. **Template** loads your state's field list instead. The plugin adds a few of its own columns (street link, the road it was measured on, access location, side of road). Leave those out of your NENA export if your exchange doesn't expect them.

### Place an address

1. Click **Address** on the toolbar.
2. **Click the road** where the address is reached. Snapping follows your QGIS snap settings. Clicking an existing driveway uses that driveway's access point.
3. **Click the building.**
4. A dialog shows the house number, street, community values, and any warnings. You can change the number, suffix, unit, and placement before creating the point.

The access point is written 10 feet straight off the road click, on the building's side. It is linked to the address and to the centerline. Use **Driveway** when you want the path drawn.

| Tip | |
|---|---|
| Clicked the wrong spot? | Press **Backspace** or **Esc** |
| Building right next to the road? | Building clicks never snap onto a road line, so the side is always read from where you actually clicked |
| Done? | **Esc** again, or right-click, to leave the tool |
| Made a mistake? | **Undo** removes the address, plus any driveway or access point made with it (access points are kept if something else still uses them) |

If you delete an access point that addresses still use, the plugin puts it back and tells you which addresses to move first.

### Draw a driveway

1. Click **Driveway** on the toolbar.
2. Click the road (or an existing driveway to branch off it).
3. Click each bend.
4. Right-click or double-click to finish. **Backspace** removes the last bend, and **Esc** starts over.

The next address you place on that driveway shares its access point.

### Place units (apartments, townhomes)

1. Click **Units** on the toolbar.
2. Pick a **Unit type** (Apt, Unit, Suite…) in the small window that opens.
3. Click the road or driveway once. The window shows the house number for **both** sides of the road, for example *L 151 / R 150*.
4. Click the first unit. **Its side of the road picks the house number** (odd or even), and every unit under that roof keeps it.
5. Click the rest of the units. The **unit number** counts up: 1, 2, 3 / 101, 102, 103 / A, B, C, and other styles.
6. Right-click or **Esc** finishes this building. **Esc** again leaves the tool.

If you type your own house number and it's odd on the even side (or the other way round), you'll get a warning.

**Straight driveway for each unit** is off by default. Turn it on only when each unit needs its own line.

### How the house number is chosen

Everything is worked out **on the segment you click**. Other segments of the same street don't change the number.

Set the method under **Settings → Address rules**. The first unit in a Place units session uses the same rules.

| Step | What happens |
|---|---|
| **Left or right** | Comes from the direction the segment was drawn. Stand at its first point (green dot) facing its last point (red square): left is left |
| **Odd or even** | Comes from your settings: by default **odd on the left, even on the right**. If the segment's own Parity_L/Parity_R says otherwise, you get a warning and your settings are still used. Re-range the segment to fix it. (To let the segment's parity win instead, tick that option on Address rules.) A side marked **Z** gets no addresses |
| **The number: Calculated** (default) | The segment's **From** number on that side, plus one number for every set distance from the segment's first point. 5.28 ft per number is about 1,000 numbers per mile. A segment with no From number starts at the start number you set |
| **The number: Interpolated** | Placed between the segment's **From** and **To** on that side, in proportion to how far along the segment you clicked |
| **The number: By frontage** | The segment's From, plus a fixed count of numbers for each lot width from the segment's first point |
| **Order** | Stays between the addresses already on that side of the **same segment**, so numbers keep increasing along it |
| **Number already taken** | Takes the next free number, adds a suffix (101A), or asks you. If the number is already used on another segment of the street, you're warned |
| **Different rules by area** | A community, ESN, ZIP, city, or county can have its own method or spacing |

If the number falls outside the segment's From–To on that side, the dialog tells you and suggests re-ranging.

### ZIP, community, and ESN

Under **Settings → Sides**, pick your ZIP, MSAG community, and ESN polygon layers. A road is often a boundary, so left and right can differ.

- **Calculate sides** writes those values to each side of the road.
- **Split where the side changes** cuts the road where a boundary crosses it, and updates the addresses on those pieces.
- **Copy road side** gives existing address points the values from their side of the road.
- New addresses copy their side automatically.

**Map markers:** when address points are selected, the map shows where each one meets the road. Blue means OK. Red means the road has moved away from the access point. Hollow grey means the access point was estimated. Nothing is written. Clear the selection and the markers go away.

### Renaming part of a road

If you assign some road pieces to a different street and they already have addresses, a dialog lists those addresses with their new numbers. Keep the old numbers, re-address all of them, or re-address only the ones you tick.

### Reports

- **Streets report**: a CSV of every street, with its status, alias, piece count, address count, and number range.
- **Addresses by community**: counts addresses by MSAG, ZIP, ESN, or city.

---

## Address ranges on roads

Open **NENA ▾ → Ranges**.

**The basics (all methods):**

- Draw roads in the direction numbers go up. **From** is at the start of the line.
- Left and right follow that direction. Usually odd numbers are on the left and even on the right. Existing parity, including **Z** (no addresses), is kept unless you turn that off.
- **Street chains** numbers connected pieces of the same street in order, so they don't overlap. **Each feature** numbers every piece on its own.

| Method | What you get | Good for |
|---|---|---|
| **Hundred-block** | Each piece gets the next block: 100–198/101–199, then 200–298/201–299… | City grids |
| **Interval** | Numbers go up by distance (5.28 ft ≈ 1,000 per mile) | Rural distance-based addressing |
| **Proportional** | Spreads a low–high range across the street by length, and can bend around existing addresses | Fitting existing numbers |
| **From address points** | Builds the range from the house numbers near each piece | Data with good address points |

**Range selected** and **Range layer** run as one undo step.

**Fill range gaps** extends a piece so its numbers meet the next piece of the same street. Overlaps are left alone. The gap tolerance is set on **Check → Checks**.

Only features whose values actually change are edited.

---

## When you edit a road

Turn these on or off under **Settings → Editing**.

| You… | The plugin… |
|---|---|
| **Split** a road with QGIS | Divides the original range across the pieces by length. The first piece keeps **From**, the last keeps **To**. Undo once to restore the numbers, again to undo the split. If an address-point layer is set on **Ranges → Method**, houses keep their numbers on the piece they actually front |
| Use the **Split** tool | One click: hover to preview, click to split. Both pieces are re-ranged and the new piece gets a new ID. One undo step |
| **Draw a new road** that ends on or crosses another road | Splits the existing road there and continues the range. Turn off "crossings" where you have overpasses |
| Have **old roads that cross** without being split | **Split at intersections** finds and splits them, then re-ranges |
| **Merge** roads | With **Combine ranges after Merge** on, the merged road gets the lowest From and highest To. *Select the pieces before you merge.* |
| **Reshape** a road | Can recompute the high number from the new length, or from nearby addresses |
| Split with another tool | **Redistribute** spreads the combined range over the selected pieces |

**IDs:** enter your agency ID under **Settings → Editing**. **Fill empty** gives roads without one a NENA ID. Pieces created by a split always get a new ID.

---

## Checking your data

Click **Validate** on the toolbar, or open **NENA ▾ → Check**.

The **Results** page lists every problem. Double-click a row to zoom to it. You can select the features, export a CSV, or load the problems as a temporary layer. Overlapping ranges are selected automatically.

**What it looks for:**

- Missing required values, or a parity that isn't O, E, B, or Z
- Ranges that are missing, negative, half-filled, or backwards
- Two pieces of the same street claiming the same numbers, or gaps between pieces
- Pieces of one street drawn in opposite directions
- Parity that doesn't match the numbers on that side
- Missing, duplicate, or badly formed IDs
- Empty, zero-length, or broken geometry
- Names that don't match the master street list

**Address checks** (from the Addresses window) catch numbers outside the road's range, the wrong odd/even side, numbers out of order, duplicates, access points that have moved off the road, missing streets, and ZIP/community/ESN values that don't match the road side.

**MSAG comparison** (**Check → MSAG**): load your telephone company's MSAG into the project, pick its columns, and compare. `MAIN ST` matches `Main Street`. MSAG rows that aren't on your map are listed too.

---

## Settings

Open **NENA ▾ → Settings**. These are things you set once. **They save automatically.**

| Page | What you set |
|---|---|
| **Fields** | Match your road layer's columns to NENA names |
| **Sides** | ZIP, MSAG community, and ESN polygon layers |
| **Defaults** | Values like state, county, and one-way that fill **only empty** fields. Leave parity blank if you want ranging to set it |
| **Street rules** | When two names count as the same, and what a rename does |
| **Address rules** | How a new address gets its number |
| **Editing** | What happens when you split, merge, or add a road, plus IDs |

**More on Fields:**

- **Add mandatory** / **Add all fields** create missing columns. Names stay short enough for shapefiles.
- **Street key** decides which pieces belong to the same street. The default is all the name parts together.
- **Field template** loads your state's column list from a GeoPackage, shapefile, GeoJSON, layer, JSON, or CSV. See the `examples` folder. **Back to the built-in NENA schema** clears it.
- The field match is stored on the layer, so it travels with the project. **Export JSON** and **Import JSON** reuse it elsewhere.
- The full field list is in [`docs/NENA_SCHEMA.md`](docs/NENA_SCHEMA.md).

---

## New tables, importing, and edit history

Open **NENA ▾ → Schema and import**.

### Create a fresh set of NENA tables

Click **Create schema** and choose where the tables go:

| Destination | What you pick |
|---|---|
| GeoPackage | One `.gpkg` file |
| File geodatabase | A folder ending in `.gdb` |
| Database | A connection already set up in QGIS. The schema (like `public` or `dbo`) is created if needed |

Pick one coordinate system. A table name prefix is optional.

| Table | What it holds |
|---|---|
| RoadCenterline | Road lines and their ranges |
| StreetNames | The master street-name list |
| SiteStructureAddressPoint | One point per address |
| Driveway | Driveway paths, when you draw them |
| AccessPoint | Where driveways leave the road |
| RoadType, UnitType, AddressStatus, RoadStatus, AddressType | Drop-down lists, pre-filled with standard values |
| NENA_Domains | Local drop-down lists (counties, communities, ZIPs, ESNs, road classes…) |

ZIP, MSAG, and city boundary polygons are not created here. Use the ones you already have.

**Replace tables that already exist** overwrites tables with the same names. Other tables are untouched.

### Import your existing data

**Import** copies features from layers in your project into the new tables.

- Columns are matched by NENA name and common older names. Click **Fields** to adjust the match.
- You can import only the selected features.
- Geometry is reprojected for you.
- Required fields that couldn't be matched are reported.

### Who made the edit

On PostgreSQL, SQL Server, Oracle, and SAP HANA, saves record the database login:

- **Who entered** is filled once, on new features.
- **Last updated by** is updated on every save.

GeoPackages, file geodatabases, and shapefiles have no login, so these fields are left alone.

### Edit history (versions)

Click **Versions** to create history tables, then turn on **Track changes**. Every time you save a road, street name, address, driveway, or access point, a history row is added.

| You saved… | History records… |
|---|---|
| A new feature | The feature, as version 1 |
| A change | The new feature, plus the old and new values of what changed (including shape) |
| A delete | The feature that was removed |

To see one feature's history, filter the history table by its ID and sort by version. Drop-down lists aren't tracked. Turn **Track changes** off at any time; the history tables stay.

---

## Good to know

- **This is an editing tool, not a NENA certification.** Check your 911 authority's own requirements before an official submission.
- **Roads should meet at their ends.** A road crossing the middle of another isn't an intersection until it's split. The plugin can split it for you.
- **A piece drawn backwards** starts its own numbering. Validate flags it, and **Reverse** fixes it.
- **Select pieces before merging**, so the plugin still knows their ranges.
- **Overpasses:** turn off splitting on crossings where roads pass over each other. T-intersections are still split.
- **Only edits made in this QGIS session** are re-ranged automatically, not edits from other programs.
- **Undo is per layer.**
- **House-number suffixes** (101A, 101-1) aren't used for ranging. Only the leading digits count.
- **Field templates** bring field names, types, and required flags, but not domains or default values.
- **Not in this version:** a Processing toolbox algorithm, snapping the building click to footprints, rewriting street types already on features, or a dangles/overshoots topology check.

---

## Words used in this guide

| Term | Meaning |
|---|---|
| **Centerline (RCL)** | A road line. Each piece carries From/To address numbers on its left and right sides |
| **Range** | The From–To house numbers on one side of a road piece |
| **Parity** | Which numbers a side gets: **O**dd, **E**ven, **B**oth, or **Z**ero (no addresses) |
| **Left / Right** | Sides of the road, looking in the direction the line was drawn |
| **Access point** | Where a driveway leaves the road. Shared access points serve several addresses |
| **SSAP** | Site/Structure Address Point, NENA's name for an address point |
| **ESN** | Emergency Service Number, the code for which fire/EMS/police respond |
| **MSAG** | Master Street Address Guide, the phone company's table of valid address ranges |
| **NGUID** | NENA Globally Unique ID, the permanent ID on each feature |
| **Code / Label** | In a drop-down, the **code** is what's saved and the **label** is what you see |
| **Edit buffer** | Changes in QGIS that aren't saved to the file or database yet |

---

## What's new

- **3.6.3** — Place address saves an access point 10 feet straight off the road click, on the building's side, linked to the address and the centerline. Draw driveway is the tool for the path.
- **3.6.2** — A new house number follows where you click, counting from the start of the line (the green dot). Coordinates stored in a SQL Server float column had been saved as whole degrees, so every existing address looked like it sat at the start of the street and the new number was pushed up to the last house.
- **3.6.1** — New **One-way ▾** tool marks selected roads B, FT, or TF for routing without touching the line or its numbers. One-way roads show red travel arrows in the NENA style.
- **3.6.0** — House numbers are worked out on the segment you click: **Calculated** counts from that segment's From number and first point (for example one number every 5.28 ft), **Interpolated** uses its From/To. Addresses on other segments of the same street no longer push the number. Odd/even always follows your settings (odd on the left, even on the right); a segment whose Parity_L/R disagrees is reported, not followed. **Reverse and re-range** is now the default and ranges the reversed roads with your parity settings.
- **3.5.2** — Fixed: an address on a separate, unconnected piece of the same street could push a new house number far too high ("raised above 418"). Numbers are now only compared along connected pieces of a street. Fixed a crash when closing QGIS 4 (or removing a layer) while the plugin was watching that layer. Dialogs such as **New address** now use a normal light background too.
- **3.5.1** — Fixed: Place units could give units on the right side of a road the left side's odd number. The road click sits on the centerline, so its side couldn't be known; the first unit you click now picks the side. Building and unit clicks no longer snap onto road lines (or onto a cross street at an intersection), which could also put an address on the wrong side.
- **3.5.0** — **Style NENA layers** gives roads a road look with direction arrows, a green start dot, a red end square, street names, and From/To numbers at each end. Address points become labelled houses, driveways brown dashes, access points blue diamonds. New schema layers are styled automatically. The new **Reverse** tool flips selected roads and swaps their left/right values and ranges so every house keeps its number, flips one-way, and switches the side on linked address and access points. **Reverse and re-range** also ranges them again.
- **3.4.0** — Drop-down lists for many more fields: county, municipality, communities, ZIP and postal community (narrowed by ZIP), ESN, MSAG community, road class, placement, directionals, parity, one-way, country, and state, on both sides of centerlines. The Lookup tables window shows every list, creates missing tables, and loads values from your data. Values are edited in place and saved right away. Windows have a normal light background, remember their size, and can be docked. The toolbar has a **NENA ▾** window menu and a name under each tool. Settings save automatically.
- **3.3.1** — Place units keeps the house number from the road click; only the unit number changes. Toolbar split into centerlines and addressing.
- **3.3.0** — Access points get their own table. Place units assigns a unit type. Road types, unit types, statuses, and address types are editable lookup tables. Each tool opens in its own window.
- **3.2.10** — This guide, in plain language.
- **3.2.9** — Place address, Draw driveway, and Place units are separate tools. Driveways only when you ask. Editable street-type list.
- **3.2.8** — Windows organized into Streets, Addresses, Ranges, Check, and Settings.
- **3.2.7** — Who entered / Last updated by use the database login.
- **3.2.6** — Split where the community side changes, split crossing roads, close range gaps, compare with an MSAG, fill empty fields from defaults.
- **3.2.5** — ZIP, MSAG, and ESN come from the polygon on each side of the road.
- **3.2.4** — Each save can record a numbered version.
- **3.2.3** — Building the street list skips unnamed roads.
- **3.2.2** — Splits divide the original range. New addresses get the full road name.
- **3.2.1** — Scrolling panels and a toolbar on its own row.
- **3.2.0** — Optional history tables.
- **3.1.0** — Create new tables and import existing data.
- **3.0.0** — Click-to-address, with driveways, community values, and address checks.
- **2.0.0** — Master street names with rename across every linked feature.
- **1.0.0** — Field mapping, address ranges, and validation.

---

## For developers

This repository is the plugin itself. The copy QGIS loads must be named `nena_rcl_tools`.

The tests don't need QGIS. Most import the plugin as `nena_rcl_tools`, and two import it by its source folder name, so run them from the parent folder:

```powershell
New-Item -ItemType Directory -Force -Path "$env:TEMP\nena_py" | Out-Null
cmd /c mklink /J "$env:TEMP\nena_py\nena_rcl_tools" "$PWD\nena_addressing_manager"
$env:PYTHONPATH = "$env:TEMP\nena_py;$PWD"
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest nena_rcl_tools/test -q
cmd /c rmdir "$env:TEMP\nena_py\nena_rcl_tools"
```

> ⚠️ Remove the junction with `cmd /c rmdir`. Deleting it in File Explorer can delete your source folder.

The NENA field list is in [`docs/NENA_SCHEMA.md`](docs/NENA_SCHEMA.md).
