# Vector Data Office Boy

![Vector Data Office Boy Hero Banner](docs/assets/hero_banner.png)

[![CI Pipeline](https://github.com/Anovvy/vektor-data-office-boy/actions/workflows/ci.yml/badge.svg)](https://github.com/Anovvy/vektor-data-office-boy/actions/workflows/ci.yml)
[![QGIS Minimum Version](https://img.shields.io/badge/QGIS-%3E%3D%203.22-589632.svg)](https://qgis.org/)
[![Plugin Version](https://img.shields.io/badge/Version-1.0.0-blue.svg)](https://github.com/Anovvy/vektor-data-office-boy/releases)
[![License: GPL-2.0-or-later](https://img.shields.io/badge/License-GPL--2.0--or--later-orange.svg)](LICENSE)

## Overview and Problem Statement

Spatial planning datasets (such as Indonesian RTRW zoning maps) and land-cover polygons frequently accumulate topological defects during multi-editor digitizing, format conversions, and geoprocessing dissolves. These defects include figure-8 bow-tie self-intersections, acute digitizing spikes, partial and 100% contained overlaps, unattributed interior gaps, and microscopic or needle-thin sliver polygons. Manual vertex editing across hundreds or thousands of features is slow, error-prone, and often introduces secondary topological conflicts.

**Vector Data Office Boy** is an all-in-one QGIS 3.x plugin engineered to automate polygon topology auditing and geometry cleaning from a single unified non-modal dialog and QGIS Processing provider:

- **Zero External Runtime Dependencies**: Uses standard PyQGIS and Shapely/GEOS libraries bundled with QGIS so end users can install directly from the QGIS Plugin Manager or a release ZIP without running `pip install`.
- **Automatic Metric CRS Normalization**: Layers stored in geographic coordinates (such as `EPSG:4326` degrees) or non-metric projections are transparently projected to an appropriate local UTM metric CRS for accurate area (`m2`), perimeter (`m`), and Polsby-Popper calculations, then converted back to the source CRS with Z-dimension (`PolygonZ`) preservation.
- **Non-Closing Interactive Workflow**: Each of the four ordered topology sections provides independent **Scan** and **Fix It** buttons without closing the dialog window, allowing rapid scan-inspect-repair iteration.
- **Atomic In-Place Overwrite**: Every **Fix It** execution writes to a staged temporary file and atomically replaces the source vector dataset on disk (or updates a live QGIS vector layer cleanly), removes stale shapefile spatial index sidecars (`.sbn`, `.sbx`, `.qix`), and reloads/repaints the map canvas immediately.

---

## Core Topology Tools and Scan Output Colors

Vector Data Office Boy enforces a deterministic four-stage topology cleaning order:

| Order | Section | Error Type Key | Temporary Memory Layer Name | Fill Color | Stroke Color | Scan Output | Fix It Behavior |
|---|---|---|---|---|---|---|---|
| 1 | Ring Self-Intersection | `RING_SELF_INTERSECTION` | `Error_Ring_Self_Intersection` | Orange (`#FF8C00`) | `#B35900` | Circular `5 m` marker polygons at invalid ring crossings and acute boundary spikes | Repairs invalid rings via structure-preserving `make_valid` + polygonal filtering, and optionally prunes acute boundary spikes |
| 2 | Overlap Polygon | `OVERLAP` | `Error_Overlap_Polygon` | Red (`#FF2400`) | `#800000` | Exact polygonal footprint of pairwise intersections and 100% contained overlaps | Cuts interior holes in host polygons for contained enclaves (Option A default) and eliminates partial/micro overlaps via precision coverage noding |
| 3 | Internal Gap | `GAP` | `Error_Internal_Gap` | Purple (`#9B30FF`) | `#4B0082` | Exact geometry of interior voids extracted strictly from `unary_union.interiors` | Merges every internal gap into an adjacent polygon while strictly preserving outer dataset boundaries (`exterior`) |
| 4 | Sliver Polygon | `SLIVER_POLYGON` | `Error_Sliver_Polygon` | Yellow (`#FFD700`) | `#997A00` | Standalone micro/thin slivers and tiny disconnected `MultiPolygon` sub-parts | Prunes micro `MultiPolygon` fragments and merges standalone slivers into touching neighbors |

### Diagnostic Scan Attribute Schema (11 Columns)

Every temporary memory layer produced by **Scan** uses a single-symbol fill renderer matching the section color above and exposes the following canonical 11-column schema (`details` and `fix_method` are intentionally omitted):

| Column | Type | Description |
|---|---|---|
| `error_id` | Integer | Sequential 1-based error index within the scan run |
| `error_type` | String | Canonical error type (`RING_SELF_INTERSECTION`, `OVERLAP`, `GAP`, or `SLIVER_POLYGON`) |
| `severity` | String | Diagnostic severity level (`CRITICAL`, `HIGH`, or `MEDIUM`) |
| `fid_1` | Integer | Primary feature ID associated with the topology issue |
| `fid_2` | String | Secondary feature ID, comma-separated neighbor FIDs, or `part_<idx>` for multipart fragments |
| `oid_ref` | String | Human-readable `OBJECTID`/`OID`/`ID` attribute reference (falls back to FID) |
| `area_m2` | Double | Metric area of the error or marker polygon in square meters |
| `perim_m` | Double | Metric perimeter of the error or marker polygon in meters |
| `pp_index` | Double | Polsby-Popper compactness index `4 * pi * area / perimeter^2` (`0.0` to `1.0`) |
| `error_x` | Double | Representative X coordinate of the error in the source layer CRS |
| `error_y` | Double | Representative Y coordinate of the error in the source layer CRS |

### QGIS Processing Toolbox Integration

All four **Scan** and four **Fix** operations are also registered under **Processing Toolbox -> Vector Data Office Boy**:
- **Topology Audit (Scan)**: `1. Scan Ring Self-Intersections`, `2. Scan Overlap Polygons`, `3. Scan Internal Gaps`, `4. Scan Sliver Polygons`
- **Geometry Cleaning (Fix)**: `1. Fix Ring Self-Intersections (In-Place)`, `2. Fix Overlap Polygons (In-Place)`, `3. Fix Internal Gaps (In-Place)`, `4. Fix Sliver Polygons (In-Place)`

---

## Installation Guide

### Option 1: Official QGIS Plugin Repository (Recommended for End Users)

1. Open **QGIS Desktop** (3.22 or newer).
2. Navigate to **Plugins -> Manage and Install Plugins...**.
3. Open the **All** tab and search for **Vector Data Office Boy**.
4. Click **Install Plugin**, then launch it from **Plugins -> Vector Data Office Boy -> Vector Data Office Boy** or the plugin toolbar icon.

### Option 2: Install from GitHub Releases ZIP

1. Download `vector_data_office_boy.zip` and `SHA256SUMS.txt` from the [GitHub Releases](https://github.com/Anovvy/vektor-data-office-boy/releases) page.
2. Optionally verify the archive checksum:
   ```bash
   sha256sum -c SHA256SUMS.txt
   ```
3. In QGIS, open **Plugins -> Manage and Install Plugins... -> Install from ZIP**.
4. Browse to `vector_data_office_boy.zip` and click **Install Plugin**.

### Option 3: Local Developer Installation and PyQGIS Verification

Developers working from a cloned repository can install the plugin directly into the active QGIS profile (`default` by default) and run an offscreen PyQGIS smoke check:

```powershell
# Windows (using QGIS LTR Python)
& "C:\Program Files\QGIS 3.44.15\bin\python-qgis-ltr.bat" scripts\install_to_local_qgis.py --verify
```

```bash
# Linux / macOS
python3 scripts/install_to_local_qgis.py --verify
```

---

## User Guide and Complete Parameter Reference

The plugin window is organized into two primary tabs (**Parameters** and **Log**) plus a shared progress bar and **Close** button at the bottom:

- **Parameters Tab**: Left scroll pane containing General Parameters and Sections 1-4; right-hand **Parameter Help** HTML panel (`QTextBrowser`) that updates automatically whenever you hover, click, focus, or modify any control.
- **Log Tab**: Top **Topology Run Summary** table (`Section`, `Action`, `Count`, `Total Area (m2)`, `Status`) and bottom **Execution Console** with timestamped progress and diagnostic counts.

### General Parameters

| Control | Widget / Type | Default | Description |
|---|---|---|---|
| **Input Polygon Layer** | `QComboBox` (`cmbInputLayer`) | Active polygon layer | Selects the target `Polygon`/`MultiPolygon` layer in the QGIS project. Temporary diagnostic layers generated by this plugin are excluded automatically. |
| **Refresh** | `QPushButton` (`btnRefreshLayers`) | - | Synchronizes the layer dropdown with currently loaded QGIS polygon layers while preserving your selection. |
| **Recalculate area and perimeter attribute fields after fix** | `QCheckBox` (`chkRecalcAttrs`) | `Checked` (`True`) | When checked, automatically recomputes `LUAS_HA` (`area_m2 / 10000`, 4 decimals), `Shape_Area` (`m2`), and `Shape_Le_1` (`m`) when those columns exist on the layer. Uncheck to keep tabular values frozen. |

### Section 1: Ring Self-Intersection (Orange `#FF8C00`)

| Control | Widget / Type | Default | Description |
|---|---|---|---|
| **Prune extreme boundary spikes** | `QCheckBox` (`chkPruneSpikes`) | `Checked` (`True`) | Enables detection and pruning of acute needle-like boundary kickback vertices in addition to GEOS invalid ring self-intersections. |
| **Minimum Spike Vertex Angle (deg)** | `QDoubleSpinBox` (`spinSpikeAngle`) | `1.00 deg` (`0.05` to `45.00`) | Interior vertex angle ceiling below which consecutive boundary vertices (with arm length `< 5.0 m`) are flagged as extreme spikes. Increase (`3.0 - 8.0 deg`) for coarse digitizing sawtooth glitches; decrease (`0.1 - 0.5 deg`) to protect narrow cadastral corners. |
| **Scan / Fix It** | `QPushButton` (`btnScanSelfIntersect`, `btnFixSelfIntersect`) | - | Audits rings (`Error_Ring_Self_Intersection` in `#FF8C00`) or repairs self-intersections and spikes in place. |

### Section 2: Overlap Polygon (Red `#FF2400`)

| Control | Widget / Type | Default | Description |
|---|---|---|---|
| **Overlap Resolution Rule** | `QComboBox` (`cmbOverlapRule`) | `Cut Hole in Host Polygon (Keep All Features)` | Strategy used to resolve overlaps during **Fix It** (see options below). |
| **Minimum Overlap Area (m2)** | `QDoubleSpinBox` (`spinMinOverlapArea`) | `0.000100 m2` (`0.000000` to `1000000.000000`) | Minimum intersection area required to flag/repair an overlap when micro-overlaps are excluded. |
| **Include micro-overlaps below minimum area threshold** | `QCheckBox` (`chkIncludeMicroOverlap`) | `Unchecked` (`False`) | When checked, flags and eliminates all polygonal overlaps strictly greater than `0.0 m2` regardless of the minimum area threshold. |
| **Scan / Fix It** | `QPushButton` (`btnScanOverlap`, `btnFixOverlap`) | - | Audits pairwise/contained overlaps (`Error_Overlap_Polygon` in `#FF2400`) or resolves all overlaps in place. |

**Overlap Resolution Rule Options:**
1. **Cut Hole in Host Polygon (Keep All Features)** *(Default / Option A)*: For 100% contained overlaps (an enclave polygon inside a larger host polygon), carves the exact footprint of the enclave as an interior hole inside the host polygon so both features survive with zero overlap. Partial peer overlaps are resolved cleanly via precision coverage noding.
2. **Subtract Overlap from Larger Polygon**: Subtracts overlapping zones from whichever intersecting polygon has the larger area, leaving smaller features untouched.
3. **Keep Lower FID (Subtract from Higher FID)**: Preserves lower-FID features verbatim and subtracts overlap regions from higher-FID features (including removing higher-FID features completely contained inside lower-FID hosts).

### Section 3: Internal Gap (Purple `#9B30FF` - Outer Boundary Preserved)

| Control | Widget / Type | Default | Description |
|---|---|---|---|
| **Gap Merge Rule** | `QComboBox` (`cmbGapRule`) | `Longest Shared Boundary` | Selects which adjacent polygon absorbs each interior void inside `unary_union.interiors`. Outer dataset boundaries (`exterior`) are never altered. |
| **Scan / Fix It** | `QPushButton` (`btnScanGap`, `btnFixGap`) | - | Audits interior voids (`Error_Internal_Gap` in `#9B30FF`) or closes all internal gaps in place. |

**Gap Merge Rule Options:**
1. **Longest Shared Boundary** *(Default)*: Merges each internal gap into the touching neighbor polygon sharing the longest perimeter contact edge.
2. **Largest Neighbor Area**: Merges each internal gap into the adjacent polygon with the largest total area.
3. **Smallest Neighbor Area**: Merges each internal gap into the adjacent polygon with the smallest total area.

### Section 4: Sliver Polygon (Yellow `#FFD700`)

| Control | Widget / Type | Default | Description |
|---|---|---|---|
| **Absolute Sliver Area Threshold (m2)** | `QDoubleSpinBox` (`spinSliverArea`) | `10.0000 m2` (`0.0000` to `10000000.0000`) | Standalone polygons with area `<=` this threshold are classified as micro-slivers regardless of shape. |
| **Polsby-Popper Compactness Threshold** | `QDoubleSpinBox` (`spinSliverPP`) | `0.0550` (`0.0001` to `1.0000`) | Standalone polygons with compactness `PP <=` this threshold (and area `<=` Max Area below) are classified as thin slivers. |
| **Max Area for Compactness Rule (m2)** | `QDoubleSpinBox` (`spinSliverMaxAreaPP`) | `150.00 m2` (`0.00` to `10000000.00`) | Upper area cap for shape-based Polsby-Popper sliver detection, protecting large linear features such as major rivers or highways. |
| **Min MultiPolygon Part Area (m2)** | `QDoubleSpinBox` (`spinPartMinArea`) | `1.0000 m2` (`0.0000` to `1000000.0000`) | Individual disconnected parts inside a `MultiPolygon` with area `<=` this threshold are flagged and cleaned. |
| **Sliver Merge Rule** | `QComboBox` (`cmbSliverRule`) | `Longest Shared Boundary` | Strategy used to select which touching neighbor absorbs each standalone sliver. |
| **Scan / Fix It** | `QPushButton` (`btnScanSliver`, `btnFixSliver`) | - | Audits standalone slivers and tiny multipart fragments (`Error_Sliver_Polygon` in `#FFD700`) or cleans and merges them in place. |

**Sliver Merge Rule Options:**
1. **Longest Shared Boundary** *(Default)*: Merges the sliver into the touching neighbor sharing the longest common edge.
2. **Largest Neighbor Area**: Merges the sliver into the touching neighbor with the largest total area.
3. **Same Attribute Class (TEMA/JENIS)**: Prefers merging the sliver into an adjacent neighbor sharing matching `TEMA` and `JENIS` attribute values, falling back to longest shared boundary when no neighbor matches.

---

## Customizing UI with Qt Designer

The dialog layout is defined in `vector_data_office_boy/ui/office_boy_dialog_base.ui` using **100% standard Qt5 widgets** (no `<customwidgets>` or `Qgs*` classes). You can open and edit it directly inside standalone **Qt Designer** (`designer.exe` or `qt5-designer`) to adjust spacing, typography, or layout proportions.

Do **not** rename or remove any of the protected `objectName` values below, as `office_boy_dialog.py` binds signals and contextual help directly to them:

| Category | Protected `objectName` Values |
|---|---|
| Tabs and Containers | `tabMain`, `tabParameters`, `tabLog`, `splitterParameters`, `grpGeneral`, `grpSelfIntersection`, `grpOverlap`, `grpInternalGap`, `grpSliver`, `grpHelp`, `grpSummary`, `grpConsoleLog` |
| General Controls | `cmbInputLayer`, `btnRefreshLayers`, `chkRecalcAttrs` |
| Section 1 Controls | `chkPruneSpikes`, `spinSpikeAngle`, `btnScanSelfIntersect`, `btnFixSelfIntersect` |
| Section 2 Controls | `cmbOverlapRule`, `spinMinOverlapArea`, `chkIncludeMicroOverlap`, `btnScanOverlap`, `btnFixOverlap` |
| Section 3 Controls | `cmbGapRule`, `btnScanGap`, `btnFixGap` |
| Section 4 Controls | `spinSliverArea`, `spinSliverPP`, `spinSliverMaxAreaPP`, `spinPartMinArea`, `cmbSliverRule`, `btnScanSliver`, `btnFixSliver` |
| Help, Logging, and Footer | `txtParameterHelp`, `tblSummary`, `txtLog`, `btnClearLog`, `progressBar`, `btnClose` |

---

## Development, Testing, Security, and CI/CD

### Local Development Setup

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# Linux / macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
```

### Benchmark Dataset Immutability

The repository includes a real-world regional RTRW shapefile at `data_test/vector_data_test.shp` (996 polygon features in `EPSG:32748`). Automated tests **never** mutate `data_test/vector_data_test.shp` in place; `tests/conftest.py` copies all shapefile sidecars into isolated temporary directories (`tmp_path`) and verifies the SHA-256 digest of the original benchmark file remains unchanged.

### Verification Commands

```bash
# Lint checks
ruff check .

# Security SAST audit
bandit -c pyproject.toml -r scripts vector_data_office_boy

# Full test suite (PyQGIS tests run automatically when executed inside QGIS Python)
pytest -v

# Package and validate plugin ZIP + SHA256SUMS.txt
python scripts/package_plugin.py --validate --check-tag v1.0.0

# Install locally and verify inside headless PyQGIS (example on Windows QGIS LTR)
& "C:\Program Files\QGIS 3.44.15\bin\python-qgis-ltr.bat" scripts\install_to_local_qgis.py --verify
```

### CI/CD and Automated Governance Workflows

1. **CI Pipeline (`.github/workflows/ci.yml`)**: Runs Ruff linting, Bandit SAST, Pytest, and `package_plugin.py --validate` on every branch push and Pull Request targeting `main`.
2. **Antigravity PR QA Reviewer (`.github/workflows/pr_qa_review.yml` + `scripts/agy_pr_reviewer.py`)**: Uses Google Antigravity SDK (`LocalAgentConfig` with `policy.deny_all()`, `gemini-3.5-flash-lite` primary and `google-genai` fallback) to audit PR diffs against `AGENT.md` (zero-emoji policy, test immutability, Qt5 `.ui` compliance, and spatial edge-case safety).
3. **Weekly Auto-Maintainer (`.github/workflows/auto_maintenance_agent.yml` + `scripts/agy_auto_maintainer.py`)**: Runs every Monday at `02:00 UTC` (`pip-audit`, upstream QGIS release tag check via GitHub API, Pytest, and Antigravity SDK analysis). Opens a review Pull Request if `needs_action=true`; never auto-merges into `main`.
4. **Release CD Pipeline (`.github/workflows/release.yml`)**: Triggers on `v*.*.*` tag pushes, `workflow_dispatch`, or `main` pushes that bump `version=` in `vector_data_office_boy/metadata.txt` (automatically creating and pushing the corresponding `v<version>` tag if absent). Runs the full validation gate (`ruff`, `bandit`, `pytest`, `package_plugin.py --validate --check-tag`) and publishes a GitHub Release with `dist/vector_data_office_boy.zip` and `dist/SHA256SUMS.txt`.

---

## License and Author

- **License**: GNU General Public License v2.0 or later (`GPL-2.0-or-later`). See [LICENSE](LICENSE) and [vector_data_office_boy/LICENSE](vector_data_office_boy/LICENSE).
- **Author**: Bayu Nabiil (`bayunabiil1365@gmail.com`)
- **Repository**: [https://github.com/Anovvy/vektor-data-office-boy](https://github.com/Anovvy/vektor-data-office-boy)
