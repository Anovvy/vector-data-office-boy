"""PyQGIS controller for the Vector Data Office Boy non-modal topology dialog."""

from __future__ import annotations

import html
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes
from qgis.PyQt import QtCore, QtWidgets, uic
from qgis.PyQt.QtCore import QEvent, Qt

from vector_data_office_boy.processing_provider.core_bridge import (
    build_gap_config,
    build_overlap_config,
    build_self_intersection_config,
    build_sliver_config,
    ensure_core_available,
    extract_operation_summary,
    invoke_core_operation,
    register_error_layer,
)

try:
    from vector_data_office_boy.core.detector import (
        scan_internal_gaps,
        scan_overlaps,
        scan_ring_self_intersections,
        scan_slivers,
    )
    from vector_data_office_boy.core.fixer_gap import fix_internal_gaps
    from vector_data_office_boy.core.fixer_overlap import fix_overlaps
    from vector_data_office_boy.core.fixer_self_intersection import fix_ring_self_intersections
    from vector_data_office_boy.core.fixer_sliver import fix_slivers
    from vector_data_office_boy.core.models import (
        ERROR_GAP,
        ERROR_OVERLAP,
        ERROR_SELF_INTERSECTION,
        ERROR_SLIVER,
        FixResult,
        GapConfig,
        OverlapConfig,
        ScanResult,
        SelfIntersectionConfig,
        SliverConfig,
    )
    from vector_data_office_boy.core.symbology import add_or_replace_error_layer_in_project
except ImportError:  # pragma: no cover - allows inspecting dialog before core is merged
    ERROR_SELF_INTERSECTION = "ring_self_intersection"
    ERROR_OVERLAP = "overlap"
    ERROR_GAP = "internal_gap"
    ERROR_SLIVER = "sliver"
    SelfIntersectionConfig = Any  # type: ignore[assignment,misc]
    OverlapConfig = Any  # type: ignore[assignment,misc]
    GapConfig = Any  # type: ignore[assignment,misc]
    SliverConfig = Any  # type: ignore[assignment,misc]
    ScanResult = Any  # type: ignore[assignment,misc]
    FixResult = Any  # type: ignore[assignment,misc]
    scan_ring_self_intersections = None  # type: ignore[assignment]
    scan_overlaps = None  # type: ignore[assignment]
    scan_internal_gaps = None  # type: ignore[assignment]
    scan_slivers = None  # type: ignore[assignment]
    add_or_replace_error_layer_in_project = None  # type: ignore[assignment]
    fix_ring_self_intersections = None  # type: ignore[assignment]
    fix_overlaps = None  # type: ignore[assignment]
    fix_internal_gaps = None  # type: ignore[assignment]
    fix_slivers = None  # type: ignore[assignment]

_CORE_MODEL_CONTRACT = (
    ERROR_SELF_INTERSECTION,
    ERROR_OVERLAP,
    ERROR_GAP,
    ERROR_SLIVER,
    SelfIntersectionConfig,
    OverlapConfig,
    GapConfig,
    SliverConfig,
    ScanResult,
    FixResult,
    add_or_replace_error_layer_in_project,
)

_UI_PATH = Path(__file__).resolve().parent / "office_boy_dialog_base.ui"
FORM_CLASS, _ = uic.loadUiType(str(_UI_PATH))

_ERROR_SCHEMA_FIELDS = frozenset(
    {"error_id", "error_type", "severity", "oid_ref", "pp_index"}
)

_HELP_CSS = """
<style>
  body {
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.45;
    margin: 4px;
  }
  .help-header {
    border-bottom: 2px solid #3c78b5;
    padding-bottom: 6px;
    margin-bottom: 10px;
  }
  .help-title {
    font-size: 12.5pt;
    font-weight: 700;
    margin: 0 0 4px 0;
  }
  .help-meta {
    font-size: 9pt;
    opacity: 0.85;
    font-family: Consolas, "Courier New", monospace;
  }
  .badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    font-weight: 700;
    font-size: 8.5pt;
    color: #ffffff;
  }
  .section-heading {
    font-weight: 700;
    margin-top: 10px;
    margin-bottom: 3px;
  }
  ul {
    margin-top: 4px;
    margin-bottom: 6px;
    padding-left: 18px;
  }
  li {
    margin-bottom: 5px;
  }
  .example {
    font-style: italic;
    opacity: 0.92;
  }
</style>
"""


def _build_help_html(
    title: str,
    type_default: str,
    function_text: str,
    extra_sections: list[tuple[str, str]],
    *,
    accent_color: str = "#3c78b5",
    badge_text: str = "",
) -> str:
    """Format a QGIS Processing-style HTML help page."""
    badge_html = (
        f'<span class="badge" style="background-color:{accent_color};">'
        f"{html.escape(badge_text)}</span> "
        if badge_text
        else ""
    )
    sections_html = "".join(
        f'<div class="section-heading">{html.escape(heading)}</div><div>{body}</div>'
        for heading, body in extra_sections
    )
    return (
        f"<html><head>{_HELP_CSS}</head><body>"
        f'<div class="help-header" style="border-bottom-color:{accent_color};">'
        f'<div class="help-title">{badge_html}{html.escape(title)}</div>'
        f'<div class="help-meta">Type / Default: {html.escape(type_default)}</div>'
        f"</div>"
        f'<div class="section-heading">Function:</div>'
        f"<div>{function_text}</div>"
        f"{sections_html}"
        f"</body></html>"
    )


PARAMETER_HELP_DOCS: dict[str, str] = {
    "cmbInputLayer": _build_help_html(
        "Input Polygon Layer",
        "Vector Layer Selector (Polygon / MultiPolygon) | Default: Active Polygon Layer",
        "Selects the target polygon layer in the current QGIS project that will be audited by "
        "<b>Scan</b> or repaired in place by <b>Fix It</b>. Temporary diagnostic error layers "
        "created by this plugin are automatically excluded from this list.",
        [
            (
                "Supported Sources & Examples:",
                "<ul>"
                "<li><b>File-based Polygon Layers (Shapefile .shp, GeoPackage .gpkg, GeoJSON):</b> "
                "Fully supported for both non-destructive scanning and atomic in-place overwriting. "
                "<span class='example'>Example: Select a regional RTRW shapefile such as "
                "<code>vector_data_test.shp</code> (EPSG:32748) to scan and repair all four topology "
                "classes in sequence.</span></li>"
                "<li><b>Geographic CRS Layers (e.g., EPSG:4326 degrees):</b> "
                "Supported seamlessly. The core topology engine projects geometries to an appropriate "
                "metric coordinate system internally for area (m2) and compactness calculations, then "
                "writes cleaned geometries back in the source CRS. "
                "<span class='example'>Example: A national land-cover layer stored in WGS84 degrees "
                "still uses meter-squared thresholds consistently.</span></li>"
                "<li><b>Memory / Scratch Polygon Layers:</b> "
                "Supported for scanning and in-memory feature updates while testing edits prior to "
                "exporting. <span class='example'>Example: Run tests on a duplicated scratch polygon "
                "layer before touching production files.</span></li>"
                "</ul>",
            ),
        ],
    ),
    "btnRefreshLayers": _build_help_html(
        "Refresh Layer List",
        "Action Button | Non-Closing",
        "Synchronizes the <b>Input Polygon Layer</b> dropdown with all valid polygon layers "
        "currently loaded in the active QGIS project, preserving your current layer selection "
        "and updating feature counts.",
        [
            (
                "Usage Tip:",
                "Use this button after adding new shapefiles or GeoPackage layers to the map canvas "
                "while this non-modal dialog remains open.",
            ),
        ],
    ),
    "chkRecalcAttrs": _build_help_html(
        "Recalculate Area and Perimeter Attribute Fields",
        "Boolean Checkbox | Default: Checked (True)",
        "Controls whether standard geometry-derived attribute fields (such as "
        "<code>Shape_Area</code>, <code>Shape_Leng</code>, <code>Shape_Le_1</code>, "
        "<code>LUASWH</code>, <code>LUAS_HA</code>, or equivalent area/perimeter columns) are "
        "recomputed from repaired polygon geometries immediately after any <b>Fix It</b> run.",
        [
            (
                "When Checked:",
                "Automatically updates existing area (m2 or hectares) and perimeter attribute columns "
                "after overlapping cuts, gap fills, or sliver merges so attribute tables remain "
                "consistent with geometry. <span class='example'>Example: When an internal gap of "
                "25 m2 is merged into a surrounding agricultural zone in an RTRW shapefile, "
                "<code>Shape_Area</code> and <code>LUAS_HA</code> increase accordingly.</span>",
            ),
            (
                "When Unchecked:",
                "Repairs only feature geometries and preserves every existing attribute value exactly "
                "as stored. <span class='example'>Example: Keep this unchecked when tabular area "
                "columns hold legally certified deed or decree values that must not change.</span>",
            ),
        ],
    ),
    "chkPruneSpikes": _build_help_html(
        "Prune Extreme Boundary Spikes",
        "Boolean Checkbox | Default: Checked (True)",
        "Enables detection and pruning of ultra-sharp needle-like vertices (boundary kickbacks "
        "or spikes) whose interior vertex angle falls below the <b>Minimum Spike Vertex Angle</b> "
        "threshold during Section 1 Scan and Fix It.",
        [
            (
                "When Checked:",
                "Audits and removes sharp digitizing spikes in addition to invalid ring "
                "self-intersections (bow-tie twists and touching loops). <span class='example'>Example: "
                "Eliminates zero-width overshoot needles along digitized road or parcel edges caused "
                "by accidental double-clicks.</span>",
            ),
            (
                "When Unchecked:",
                "Restricts Section 1 strictly to OGC/GEOS invalid ring self-intersections and leaves "
                "sharp acute corners untouched. <span class='example'>Example: Uncheck when auditing "
                "high-detail coastlines or narrow triangular cadastral wedges with legitimate acute "
                "corners below 1 degree.</span>",
            ),
        ],
        accent_color="#FF8C00",
        badge_text="Orange #FF8C00",
    ),
    "spinSpikeAngle": _build_help_html(
        "Minimum Spike Vertex Angle (deg)",
        "DoubleSpinBox (0.05 to 45.00 deg) | Default: 1.00 deg",
        "Sets the interior vertex angle threshold (in degrees) below which three consecutive "
        "boundary vertices are flagged as an extreme spike when <b>Prune extreme boundary spikes</b> "
        "is enabled.",
        [
            (
                "When to Increase:",
                "Increase to <code>3.00 - 8.00 deg</code> when cleaning legacy digitized land-cover "
                "polygons or coarse GPS tracks containing wider V-shaped overshoot spikes along shared "
                "borders. <span class='example'>Example: A 1:50,000 digitized forest boundary with 4-degree "
                "sawtooth digitizing glitches requires raising the threshold to 5.00 deg so those spikes "
                "are pruned cleanly.</span>",
            ),
            (
                "When to Decrease:",
                "Decrease to <code>0.10 - 0.50 deg</code> when working with precision urban parcel "
                "maps (RDTR) or engineered building footprints that contain legitimate narrow corners. "
                "<span class='example'>Example: A sharp 0.8-degree triangular road junction corner should "
                "not be collapsed, so lowering the threshold to 0.25 deg protects it while still "
                "catching true 0.05-degree spikes.</span>",
            ),
        ],
        accent_color="#FF8C00",
        badge_text="Orange #FF8C00",
    ),
    "btnScanSelfIntersect": _build_help_html(
        "Scan - 1. Ring Self-Intersection",
        "Diagnostic Action Button | Non-Closing",
        "Scans all polygon rings for GEOS self-intersections and optional boundary spikes, "
        "creating or replacing an 11-column temporary memory layer styled in <b>Orange (#FF8C00)</b> "
        "in the QGIS Layers panel without closing this dialog.",
        [],
        accent_color="#FF8C00",
        badge_text="Orange #FF8C00",
    ),
    "btnFixSelfIntersect": _build_help_html(
        "Fix It - 1. Ring Self-Intersection",
        "In-Place Repair Action Button | Non-Closing",
        "Repairs invalid self-intersecting rings using <code>make_valid</code> and polygonal "
        "extraction, prunes boundary spikes (if enabled), atomically overwrites the input vector "
        "file in place, reloads the QGIS layer, and refreshes the map canvas without closing the dialog.",
        [],
        accent_color="#FF8C00",
        badge_text="Orange #FF8C00",
    ),
    "cmbOverlapRule": _build_help_html(
        "Overlap Resolution Rule",
        "ComboBox (3 Strategies) | Default: Cut Hole in Host Polygon (Keep All Features)",
        "Determines how contested intersection zones and 100% contained overlaps (enclave "
        "polygons sitting inside larger host polygons) are resolved during <b>Fix It</b>.",
        [
            (
                "Option-by-Option Breakdown:",
                "<ul>"
                "<li><b>Cut Hole in Host Polygon (Keep All Features)</b> <i>(Default / Option A)</i>: "
                "When a polygon is 100% contained inside a larger host polygon (or forms an enclave), "
                "carves its exact footprint as an interior hole inside the host polygon so both features "
                "remain intact with zero overlap. For partial peer overlaps, performs precision coverage "
                "noding to assign the overlap deterministically without deleting either feature. "
                "<span class='example'>Example: A 2-hectare urban settlement polygon situated inside a "
                "500-hectare protected forest polygon remains intact while the forest polygon receives a "
                "clean interior ring around the settlement.</span></li>"
                "<li><b>Subtract Overlap from Larger Polygon</b>: "
                "Subtracts every overlap region from whichever intersecting polygon has the larger "
                "total area, keeping the smaller polygon completely unaltered. "
                "<span class='example'>Example: Small verified water-body or substation polygons "
                "overlapping a massive generalized regional agriculture polygon retain their exact "
                "surveyed boundaries while the larger block yields the overlap strip.</span></li>"
                "<li><b>Keep Lower FID (Subtract from Higher FID)</b>: "
                "Prioritizes the feature with the lower feature ID (FID/OBJECTID) and subtracts the "
                "overlapping region from higher-FID features. "
                "<span class='example'>Example: Master base-map parcels (FID 1-200) must remain "
                "frozen while newly appended digitizing batches (FID 800+) are trimmed back against them.</span></li>"
                "</ul>",
            ),
        ],
        accent_color="#FF2400",
        badge_text="Red #FF2400",
    ),
    "spinMinOverlapArea": _build_help_html(
        "Minimum Overlap Area (m2)",
        "DoubleSpinBox (0.000000 to 1000000.000000 m2, 6 decimals) | Default: 0.000100 m2",
        "Sets the minimum intersection area (in square meters) required for an overlap to be "
        "reported and repaired when micro-overlaps are excluded.",
        [
            (
                "When to Increase:",
                "Increase to <code>0.500000 - 2.000000 m2</code> during preliminary regional RTRW "
                "audits (1:50,000) to prioritize major zoning conflicts first without cluttering the "
                "error layer with sub-meter snapping jitter. <span class='example'>Example: Set to "
                "1.000000 m2 to inspect only substantive overlaps exceeding one square meter before "
                "tightening tolerances.</span>",
            ),
            (
                "When to Decrease:",
                "Decrease to <code>0.000001 m2</code> or <code>0.000000 m2</code> when preparing final "
                "geodatabase submissions or cadastral parcels under strict zero-tolerance topology "
                "rules. <span class='example'>Example: Even a 0.00005 m2 floating-point overlap caused "
                "by shapefile coordinate truncation will be caught and cleaned.</span>",
            ),
        ],
        accent_color="#FF2400",
        badge_text="Red #FF2400",
    ),
    "chkIncludeMicroOverlap": _build_help_html(
        "Include Micro-Overlaps Below Minimum Area Threshold",
        "Boolean Checkbox | Default: Unchecked (False)",
        "Overrides the <b>Minimum Overlap Area</b> filter so every polygonal intersection greater "
        "than 0 m2 is flagged during Scan and eliminated during Fix It.",
        [
            (
                "When Checked:",
                "Includes microscopic sub-millimeter overlaps generated by coordinate precision rounding "
                "or CRS reprojection. <span class='example'>Example: Enable this before running an "
                "external database topology validator that fails on any overlap area &gt; 0.0 m2.</span>",
            ),
            (
                "When Unchecked:",
                "Filters out intersections smaller than <b>Minimum Overlap Area</b> so floating-point "
                "precision noise does not flood the Red diagnostic layer during routine mapping checks.",
            ),
        ],
        accent_color="#FF2400",
        badge_text="Red #FF2400",
    ),
    "btnScanOverlap": _build_help_html(
        "Scan - 2. Overlap Polygon",
        "Diagnostic Action Button | Non-Closing",
        "Detects pairwise and 100% contained overlaps across the selected polygon layer and adds "
        "or replaces an 11-column temporary memory layer styled in <b>Red (#FF2400)</b> without "
        "closing this dialog.",
        [],
        accent_color="#FF2400",
        badge_text="Red #FF2400",
    ),
    "btnFixOverlap": _build_help_html(
        "Fix It - 2. Overlap Polygon",
        "In-Place Repair Action Button | Non-Closing",
        "Resolves all detected overlaps according to the selected <b>Overlap Resolution Rule</b>, "
        "atomically overwrites the source dataset in place, reloads the QGIS layer, and refreshes "
        "the map canvas while keeping this dialog open.",
        [],
        accent_color="#FF2400",
        badge_text="Red #FF2400",
    ),
    "cmbGapRule": _build_help_html(
        "Gap Merge Rule",
        "ComboBox (3 Strategies) | Default: Longest Shared Boundary",
        "Selects which adjacent polygon absorbs each interior void (<code>unary_union.interiors</code>). "
        "Every internal gap enclosed between polygons is detected and filled, while outer dataset "
        "boundaries (<code>exterior</code>) are strictly preserved.",
        [
            (
                "Option-by-Option Breakdown:",
                "<ul>"
                "<li><b>Longest Shared Boundary</b> <i>(Default)</i>: "
                "Merges each internal void into the touching neighbor polygon that shares the longest "
                "common perimeter segment with that gap. "
                "<span class='example'>Example: A narrow 2 m x 120 m unattributed gap between a large "
                "rice-field zone (120 m border) and a narrow access road end-cap (2 m border) naturally "
                "merges into the rice-field zone.</span></li>"
                "<li><b>Largest Neighbor Area</b>: "
                "Merges each internal void into the adjacent polygon with the largest total area. "
                "<span class='example'>Example: Small unmapped voids scattered around small rural "
                "settlement polygons and an expansive surrounding plantation block are absorbed into the "
                "dominant plantation polygon.</span></li>"
                "<li><b>Smallest Neighbor Area</b>: "
                "Merges each internal void into the adjacent polygon with the smallest total area. "
                "<span class='example'>Example: When small surveyed infill parcels adjoin huge base "
                "land blocks and you want minor boundary voids assigned to the adjoining small parcel "
                "rather than modifying the large background polygon.</span></li>"
                "</ul>",
            ),
        ],
        accent_color="#9B30FF",
        badge_text="Purple #9B30FF",
    ),
    "btnScanGap": _build_help_html(
        "Scan - 3. Internal Gap",
        "Diagnostic Action Button | Non-Closing",
        "Extracts all interior voids enclosed strictly inside the polygon coverage (preserving outer "
        "boundaries) and outputs an 11-column temporary memory layer styled in <b>Purple (#9B30FF)</b> "
        "without closing this dialog.",
        [],
        accent_color="#9B30FF",
        badge_text="Purple #9B30FF",
    ),
    "btnFixGap": _build_help_html(
        "Fix It - 3. Internal Gap",
        "In-Place Repair Action Button | Non-Closing",
        "Merges all detected internal gaps into adjacent neighbors according to the chosen "
        "<b>Gap Merge Rule</b> without modifying any outer dataset boundary, atomically overwrites "
        "the source file in place, and reloads the QGIS layer.",
        [],
        accent_color="#9B30FF",
        badge_text="Purple #9B30FF",
    ),
    "spinSliverArea": _build_help_html(
        "Absolute Sliver Area Threshold (m2)",
        "DoubleSpinBox (0.0000 to 10000000.0000 m2) | Default: 10.0000 m2",
        "Any standalone polygon with an area strictly smaller than this threshold (in m2) is "
        "classified as a sliver polygon regardless of its compactness ratio.",
        [
            (
                "When to Increase:",
                "Increase to <code>50.0000 - 250.0000 m2</code> for medium/regional spatial planning "
                "maps (1:25,000 or 1:50,000 RTRW) where tiny standalone polygon fragments below 100 m2 "
                "cannot represent valid zoning units. <span class='example'>Example: A provincial "
                "land-cover map produced by dissolving overlay outputs often leaves 30-80 m2 residual "
                "polygons that should be merged away with a 100 m2 threshold.</span>",
            ),
            (
                "When to Decrease:",
                "Decrease to <code>0.5000 - 2.0000 m2</code> for detailed 1:1,000 cadastral surveys or "
                "urban utility infrastructure footprints. <span class='example'>Example: Real 5 m2 "
                "transformer pads or small urban alley parcels must remain intact, so lowering this "
                "threshold to 1.0 m2 protects them.</span>",
            ),
        ],
        accent_color="#FFD700",
        badge_text="Yellow #FFD700",
    ),
    "spinSliverPP": _build_help_html(
        "Polsby-Popper Compactness Threshold",
        "DoubleSpinBox (0.0001 to 1.0000, 4 decimals) | Default: 0.0550",
        "Identifies thin, elongated sliver polygons whose Polsby-Popper compactness score "
        "<code>PP = (4 * pi * Area) / (Perimeter^2)</code> falls below this value (up to the "
        "<b>Max Area for Compactness Rule</b>). A perfect circle equals <code>1.0</code>; thin "
        "needle-like slivers approach <code>0.0</code>.",
        [
            (
                "When to Increase:",
                "Increase to <code>0.0900 - 0.1500</code> when narrow belt-shaped overlap leftovers "
                "(e.g., 2 m wide by 50 m long corridor strips) remain unflagged at 0.0550. "
                "<span class='example'>Example: Misaligned administrative boundary merges often leave "
                "100 m2 ribbon strips with PP around 0.08; raising this threshold to 0.10 catches them.</span>",
            ),
            (
                "When to Decrease:",
                "Decrease to <code>0.0150 - 0.0350</code> when the dataset contains legitimate elongated "
                "linear polygons such as rivers, irrigation canals, or road corridors. "
                "<span class='example'>Example: A 120 m2 irrigation canal segment may naturally have "
                "PP = 0.04; lowering this threshold to 0.025 avoids accidentally merging the canal.</span>",
            ),
        ],
        accent_color="#FFD700",
        badge_text="Yellow #FFD700",
    ),
    "spinSliverMaxAreaPP": _build_help_html(
        "Max Area for Compactness Rule (m2)",
        "DoubleSpinBox (0.00 to 10000000.00 m2) | Default: 150.00 m2",
        "Sets the upper area ceiling (in m2) for applying the Polsby-Popper compactness rule. "
        "Polygons larger than this ceiling are exempt from shape-based sliver classification.",
        [
            (
                "When to Increase:",
                "Increase to <code>500.00 - 1500.00 m2</code> when auditing large watershed or forestry "
                "datasets that contain long 400 m2 border slivers formed by slightly offset county lines. "
                "<span class='example'>Example: A 350 m2 thin strip along a 1 km district boundary is "
                "ignored at 150 m2 but properly caught when raised to 500 m2.</span>",
            ),
            (
                "When to Decrease:",
                "Decrease to <code>30.00 - 75.00 m2</code> (or <code>0.00 m2</code> to disable compactness "
                "detection entirely) when long linear road or river polygons between 80 m2 and 150 m2 must "
                "be protected from sliver detection. <span class='example'>Example: Narrow urban lane "
                "segments around 110 m2 stay untouched when capped at 60 m2.</span>",
            ),
        ],
        accent_color="#FFD700",
        badge_text="Yellow #FFD700",
    ),
    "spinPartMinArea": _build_help_html(
        "Min MultiPolygon Part Area (m2)",
        "DoubleSpinBox (0.0000 to 1000000.0000 m2) | Default: 1.0000 m2",
        "Sets the minimum area (in m2) required for individual disconnected rings/parts inside a "
        "<code>MultiPolygon</code> feature. Parts smaller than this threshold are flagged as micro "
        "fragments and pruned during repair.",
        [
            (
                "When to Increase:",
                "Increase to <code>5.0000 - 20.0000 m2</code> when cleaning MultiPolygon land-cover "
                "classes derived from classified satellite imagery that contain isolated stray pixel "
                "specks. <span class='example'>Example: Disconnected 4 m2 single-pixel islands belonging "
                "to a distant MultiPolygon forest feature are stripped automatically at 5.0 m2.</span>",
            ),
            (
                "When to Decrease:",
                "Decrease to <code>0.0100 - 0.2500 m2</code> when MultiPolygon features legitimately hold "
                "tiny disjoint components such as bridge piers, river islets, or small monument footprints. "
                "<span class='example'>Example: Retaining 0.5 m2 survey pillar footprints grouped in a "
                "MultiPolygon infrastructure layer requires lowering this value to 0.1 m2.</span>",
            ),
        ],
        accent_color="#FFD700",
        badge_text="Yellow #FFD700",
    ),
    "cmbSliverRule": _build_help_html(
        "Sliver Merge Rule",
        "ComboBox (3 Strategies) | Default: Longest Shared Boundary",
        "Determines which neighboring polygon receives each standalone sliver polygon during "
        "<b>Fix It</b> (after pruning tiny MultiPolygon fragments).",
        [
            (
                "Option-by-Option Breakdown:",
                "<ul>"
                "<li><b>Longest Shared Boundary</b> <i>(Default)</i>: "
                "Merges each sliver into the adjacent polygon sharing the longest contact edge. "
                "<span class='example'>Example: A thin 4 m2 sliver running 30 m along an agricultural "
                "parcel and touching 0.3 m of a road merges into the agricultural parcel.</span></li>"
                "<li><b>Largest Neighbor Area</b>: "
                "Merges each sliver into whichever touching neighbor polygon has the largest total area. "
                "<span class='example'>Example: Scattered sub-5 m2 remnants between small residential "
                "lots and a vast surrounding plantation zone merge into the plantation zone.</span></li>"
                "<li><b>Same Attribute Class (TEMA/JENIS)</b>: "
                "Prefers merging the sliver into an adjacent neighbor sharing matching thematic "
                "classification fields (such as <code>TEMA</code> and <code>JENIS</code>), falling back "
                "to longest shared boundary when no touching neighbor shares the same class. "
                "<span class='example'>Example: In an RTRW layer like <code>vector_data_test.shp</code>, "
                "a 6 m2 <code>Permukiman</code> sliver bordered by both <code>Permukiman</code> and "
                "<code>Sungai</code> merges into the matching <code>Permukiman</code> neighbor to preserve "
                "thematic accuracy.</span></li>"
                "</ul>",
            ),
        ],
        accent_color="#FFD700",
        badge_text="Yellow #FFD700",
    ),
    "btnScanSliver": _build_help_html(
        "Scan - 4. Sliver Polygon",
        "Diagnostic Action Button | Non-Closing",
        "Audits the input polygon layer for standalone slivers (by area and Polsby-Popper "
        "compactness) and tiny MultiPolygon sub-parts, outputting an 11-column temporary memory "
        "layer styled in <b>Yellow (#FFD700)</b> without closing this dialog.",
        [],
        accent_color="#FFD700",
        badge_text="Yellow #FFD700",
    ),
    "btnFixSliver": _build_help_html(
        "Fix It - 4. Sliver Polygon",
        "In-Place Repair Action Button | Non-Closing",
        "Removes tiny MultiPolygon parts and merges standalone slivers into neighbors using the "
        "selected <b>Sliver Merge Rule</b>, atomically overwrites the source dataset in place, "
        "reloads the QGIS layer, and refreshes the map canvas without closing the dialog.",
        [],
        accent_color="#FFD700",
        badge_text="Yellow #FFD700",
    ),
}


class OfficeBoyDialog(QtWidgets.QDialog, FORM_CLASS):
    """Unified non-modal topology audit and cleaning dialog."""

    def __init__(self, iface: Any = None, parent: QtWidgets.QWidget | None = None) -> None:
        """Initialize UI controls, contextual help filters, and section bindings."""
        super().__init__(parent)
        self.setupUi(self)
        self.iface = iface
        self._summary_rows: dict[tuple[str, str], int] = {}
        self._help_widget_map: dict[QtCore.QObject, str] = {}
        self._signals_connected = False

        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setModal(False)
        self._configure_initial_layout()
        self._install_help_filters()
        self._connect_signals()
        self.refresh_layers()
        self._show_help_for("cmbInputLayer")

    def _configure_initial_layout(self) -> None:
        """Tune splitter proportions and summary table header behavior."""
        self.splitterParameters.setSizes([560, 440])
        header = self.tblSummary.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)

    def _install_help_filters(self) -> None:
        """Attach eventFilter and signal listeners to all interactive widgets."""
        interactive_widgets: list[tuple[str, QtWidgets.QWidget]] = [
            ("cmbInputLayer", self.cmbInputLayer),
            ("btnRefreshLayers", self.btnRefreshLayers),
            ("chkRecalcAttrs", self.chkRecalcAttrs),
            ("chkPruneSpikes", self.chkPruneSpikes),
            ("spinSpikeAngle", self.spinSpikeAngle),
            ("btnScanSelfIntersect", self.btnScanSelfIntersect),
            ("btnFixSelfIntersect", self.btnFixSelfIntersect),
            ("cmbOverlapRule", self.cmbOverlapRule),
            ("spinMinOverlapArea", self.spinMinOverlapArea),
            ("chkIncludeMicroOverlap", self.chkIncludeMicroOverlap),
            ("btnScanOverlap", self.btnScanOverlap),
            ("btnFixOverlap", self.btnFixOverlap),
            ("cmbGapRule", self.cmbGapRule),
            ("btnScanGap", self.btnScanGap),
            ("btnFixGap", self.btnFixGap),
            ("spinSliverArea", self.spinSliverArea),
            ("spinSliverPP", self.spinSliverPP),
            ("spinSliverMaxAreaPP", self.spinSliverMaxAreaPP),
            ("spinPartMinArea", self.spinPartMinArea),
            ("cmbSliverRule", self.cmbSliverRule),
            ("btnScanSliver", self.btnScanSliver),
            ("btnFixSliver", self.btnFixSliver),
        ]
        for key, widget in interactive_widgets:
            self._help_widget_map[widget] = key
            widget.installEventFilter(self)

            if isinstance(widget, QtWidgets.QDoubleSpinBox):
                line_edit = widget.lineEdit()
                if line_edit is not None:
                    self._help_widget_map[line_edit] = key
                    line_edit.installEventFilter(self)
                widget.valueChanged.connect(
                    lambda _val, k=key: self._show_help_for(k)
                )
            elif isinstance(widget, QtWidgets.QComboBox):
                widget.currentIndexChanged.connect(
                    lambda _idx, k=key: self._show_help_for(k)
                )
            elif isinstance(widget, QtWidgets.QCheckBox):
                widget.toggled.connect(
                    lambda _checked, k=key: self._show_help_for(k)
                )

    def _connect_signals(self) -> None:
        """Bind button actions and QGIS project layer events."""
        self.btnRefreshLayers.clicked.connect(self.refresh_layers)
        self.btnClearLog.clicked.connect(self._clear_log)
        self.btnClose.clicked.connect(self.close)

        self.btnScanSelfIntersect.clicked.connect(self._on_scan_self_intersection)
        self.btnFixSelfIntersect.clicked.connect(self._on_fix_self_intersection)
        self.btnScanOverlap.clicked.connect(self._on_scan_overlap)
        self.btnFixOverlap.clicked.connect(self._on_fix_overlap)
        self.btnScanGap.clicked.connect(self._on_scan_gap)
        self.btnFixGap.clicked.connect(self._on_fix_gap)
        self.btnScanSliver.clicked.connect(self._on_scan_sliver)
        self.btnFixSliver.clicked.connect(self._on_fix_sliver)

        project = QgsProject.instance()
        if project is not None and not self._signals_connected:
            project.layersAdded.connect(self._on_project_layers_changed)
            project.layersRemoved.connect(self._on_project_layers_changed)
            self._signals_connected = True

    def cleanup(self) -> None:
        """Disconnect QGIS project signals cleanly on plugin unload."""
        project = QgsProject.instance()
        if project is not None and self._signals_connected:
            try:
                project.layersAdded.disconnect(self._on_project_layers_changed)
                project.layersRemoved.disconnect(self._on_project_layers_changed)
            except (TypeError, RuntimeError):
                pass
            self._signals_connected = False

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        """Display contextual HTML documentation when hovering or focusing controls."""
        if event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.Enter,
            QEvent.Type.MouseButtonPress,
        ):
            key = self._help_widget_map.get(watched)
            if key is not None:
                self._show_help_for(key)
        return super().eventFilter(watched, event)

    def _show_help_for(self, key: str) -> None:
        """Render the HTML help page for the requested control key."""
        html_doc = PARAMETER_HELP_DOCS.get(key)
        if html_doc is not None:
            self.txtParameterHelp.setHtml(html_doc)

    def _on_project_layers_changed(self, *_args: Any) -> None:
        """Synchronize layer dropdown when QGIS project layers change."""
        self.refresh_layers()

    @staticmethod
    def _is_plugin_error_layer(layer: QgsVectorLayer) -> bool:
        """Identify temporary diagnostic error layers created by this plugin."""
        if layer.customProperty("vector_data_office_boy/error_type"):
            return True
        if layer.customProperty("vdob_error_type"):
            return True
        if layer.providerType() == "memory":
            field_names = {field.name().lower() for field in layer.fields()}
            if _ERROR_SCHEMA_FIELDS.issubset(field_names):
                return True
        return False

    def refresh_layers(self) -> None:
        """Populate cmbInputLayer with all eligible polygon layers in the project."""
        current_layer_id = self.cmbInputLayer.currentData()
        active_layer = self.iface.activeLayer() if self.iface is not None else None
        active_layer_id = (
            active_layer.id()
            if isinstance(active_layer, QgsVectorLayer)
            and active_layer.isValid()
            and active_layer.geometryType() == QgsWkbTypes.GeometryType.PolygonGeometry
            and not self._is_plugin_error_layer(active_layer)
            else None
        )

        self.cmbInputLayer.blockSignals(True)
        try:
            self.cmbInputLayer.clear()
            project = QgsProject.instance()
            if project is None:
                return

            layers = [
                layer
                for layer in project.mapLayers().values()
                if isinstance(layer, QgsVectorLayer)
                and layer.isValid()
                and layer.geometryType() == QgsWkbTypes.GeometryType.PolygonGeometry
                and not self._is_plugin_error_layer(layer)
            ]
            layers.sort(key=lambda lyr: lyr.name().lower())

            for layer in layers:
                crs_auth = layer.crs().authid() if layer.crs().isValid() else "No CRS"
                label = f"{layer.name()} [{crs_auth}] ({layer.featureCount()} features)"
                self.cmbInputLayer.addItem(label, layer.id())

            target_id = current_layer_id or active_layer_id
            if target_id:
                idx = self.cmbInputLayer.findData(target_id)
                if idx >= 0:
                    self.cmbInputLayer.setCurrentIndex(idx)
        finally:
            self.cmbInputLayer.blockSignals(False)

    def _get_selected_layer(self, *, require_non_editable: bool = False) -> QgsVectorLayer | None:
        """Retrieve and validate the currently selected polygon QgsVectorLayer."""
        layer_id = self.cmbInputLayer.currentData()
        project = QgsProject.instance()
        layer = project.mapLayer(layer_id) if (project is not None and layer_id) else None
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            QtWidgets.QMessageBox.warning(
                self,
                "Vector Data Office Boy",
                "Please select a valid polygon layer in General Parameters.",
            )
            return None

        if require_non_editable and layer.isEditable():
            QtWidgets.QMessageBox.warning(
                self,
                "Unsaved Layer Edits",
                f"Layer '{layer.name()}' currently has an active edit session in QGIS.\n\n"
                "Please save or roll back your edits before running Fix It so the source file "
                "can be overwritten safely.",
            )
            return None

        return layer

    def _set_busy(self, busy: bool) -> None:
        """Enable or disable section action controls during active execution."""
        buttons = (
            self.btnRefreshLayers,
            self.btnScanSelfIntersect,
            self.btnFixSelfIntersect,
            self.btnScanOverlap,
            self.btnFixOverlap,
            self.btnScanGap,
            self.btnFixGap,
            self.btnScanSliver,
            self.btnFixSliver,
        )
        for btn in buttons:
            btn.setEnabled(not busy)
        self.progressBar.setValue(15 if busy else 100)
        QtWidgets.QApplication.processEvents()

    def _append_log(self, message: str) -> None:
        """Append a timestamped ASCII-clean log entry to txtLog."""
        stamp = datetime.now().strftime("%H:%M:%S")
        self.txtLog.appendPlainText(f"[{stamp}] {message}")

    def _clear_log(self) -> None:
        """Clear both summary table rows and console log entries."""
        self.tblSummary.setRowCount(0)
        self._summary_rows.clear()
        self.txtLog.clear()
        self.progressBar.setValue(0)

    def _upsert_summary_row(
        self,
        section: str,
        action: str,
        count: int | str,
        total_area_m2: float | str,
        status: str,
    ) -> None:
        """Insert or update the summary row for a (Section, Action) pair."""
        key = (section, action)
        if key in self._summary_rows:
            row_idx = self._summary_rows[key]
        else:
            row_idx = self.tblSummary.rowCount()
            self.tblSummary.insertRow(row_idx)
            self._summary_rows[key] = row_idx

        area_text = (
            f"{total_area_m2:.4f}"
            if isinstance(total_area_m2, (int, float))
            else str(total_area_m2)
        )
        values = (section, action, str(count), area_text, status)
        for col_idx, val in enumerate(values):
            item = QtWidgets.QTableWidgetItem(val)
            if col_idx in (2, 3):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tblSummary.setItem(row_idx, col_idx, item)

    def _execute_scan(
        self,
        section_label: str,
        error_type: str,
        scan_fn: Callable[..., Any],
        config_factory: Callable[[], Any],
    ) -> None:
        """Run a diagnostic Scan operation without closing the dialog."""
        layer = self._get_selected_layer(require_non_editable=False)
        if layer is None:
            return

        self._set_busy(True)
        try:
            ensure_core_available()
            _ = _CORE_MODEL_CONTRACT
            config = config_factory()
            self._append_log(
                f"Starting Scan [{section_label}] ({error_type}) on '{layer.name()}'..."
            )
            scan_result = invoke_core_operation(
                scan_fn,
                layer,
                config,
                progress_callback=self.progressBar.setValue,
            )
            project = QgsProject.instance()
            if project is not None:
                register_error_layer(scan_result, project, self.iface)

            summary = extract_operation_summary(
                scan_result,
                section=section_label,
                action="Scan",
            )
            self._upsert_summary_row(
                summary.section,
                summary.action,
                summary.count,
                summary.total_area_m2,
                summary.status,
            )
            self._append_log(
                f"Scan [{section_label}] finished: {summary.count} error(s), "
                f"total area {summary.total_area_m2:.4f} m2 - {summary.status}"
            )
            for msg in summary.messages:
                self._append_log(f"  {msg}")

            if self.iface is not None and self.iface.mapCanvas() is not None:
                self.iface.mapCanvas().refreshAllLayers()
        except Exception as exc:
            self.progressBar.setValue(0)
            self._upsert_summary_row(section_label, "Scan", "-", "-", f"Error: {exc}")
            self._append_log(f"Scan [{section_label}] failed: {exc}")
            self._append_log(traceback.format_exc().rstrip())
            QtWidgets.QMessageBox.warning(
                self,
                f"Scan Failed - {section_label}",
                str(exc),
            )
        finally:
            self._set_busy(False)

    def _execute_fix(
        self,
        section_label: str,
        error_type: str,
        fix_fn: Callable[..., Any],
        config_factory: Callable[[], Any],
    ) -> None:
        """Run an in-place Fix It operation without closing the dialog."""
        layer = self._get_selected_layer(require_non_editable=True)
        if layer is None:
            return

        recalc_attrs = self.chkRecalcAttrs.isChecked()
        self._set_busy(True)
        try:
            ensure_core_available()
            config = config_factory()
            self._append_log(
                f"Starting Fix It [{section_label}] ({error_type}) on '{layer.name()}' "
                f"(recalc_attrs={recalc_attrs})..."
            )
            fix_result = invoke_core_operation(
                fix_fn,
                layer,
                config,
                recalc_attrs=recalc_attrs,
                progress_callback=self.progressBar.setValue,
            )
            summary = extract_operation_summary(
                fix_result,
                section=section_label,
                action="Fix It",
            )

            layer.reload()
            layer.triggerRepaint()
            if self.iface is not None and self.iface.mapCanvas() is not None:
                self.iface.mapCanvas().refreshAllLayers()
            self.refresh_layers()

            self._upsert_summary_row(
                summary.section,
                summary.action,
                summary.count,
                summary.total_area_m2,
                summary.status,
            )
            self._append_log(
                f"Fix It [{section_label}] finished: {summary.count} item(s), "
                f"area {summary.total_area_m2:.4f} m2 - {summary.status}"
            )
            for msg in summary.messages:
                self._append_log(f"  {msg}")
        except Exception as exc:
            self.progressBar.setValue(0)
            self._upsert_summary_row(section_label, "Fix It", "-", "-", f"Error: {exc}")
            self._append_log(f"Fix It [{section_label}] failed: {exc}")
            self._append_log(traceback.format_exc().rstrip())
            QtWidgets.QMessageBox.warning(
                self,
                f"Fix Failed - {section_label}",
                str(exc),
            )
        finally:
            self._set_busy(False)

    def _build_self_intersection_cfg(self) -> Any:
        """Build SelfIntersectionConfig from current UI inputs."""
        return build_self_intersection_config(
            prune_spikes=self.chkPruneSpikes.isChecked(),
            spike_angle=self.spinSpikeAngle.value(),
            recalc_attrs=self.chkRecalcAttrs.isChecked(),
        )

    def _build_overlap_cfg(self) -> Any:
        """Build OverlapConfig from current UI inputs."""
        return build_overlap_config(
            rule_index=self.cmbOverlapRule.currentIndex(),
            min_overlap_area=self.spinMinOverlapArea.value(),
            include_micro_overlap=self.chkIncludeMicroOverlap.isChecked(),
            recalc_attrs=self.chkRecalcAttrs.isChecked(),
        )

    def _build_gap_cfg(self) -> Any:
        """Build GapConfig from current UI inputs."""
        return build_gap_config(
            rule_index=self.cmbGapRule.currentIndex(),
            recalc_attrs=self.chkRecalcAttrs.isChecked(),
        )

    def _build_sliver_cfg(self) -> Any:
        """Build SliverConfig from current UI inputs."""
        return build_sliver_config(
            sliver_area=self.spinSliverArea.value(),
            sliver_pp=self.spinSliverPP.value(),
            sliver_max_area_pp=self.spinSliverMaxAreaPP.value(),
            part_min_area=self.spinPartMinArea.value(),
            rule_index=self.cmbSliverRule.currentIndex(),
            recalc_attrs=self.chkRecalcAttrs.isChecked(),
        )

    def _on_scan_self_intersection(self) -> None:
        """Handle Section 1 Scan click."""
        self._execute_scan(
            "1. Ring Self-Intersection",
            ERROR_SELF_INTERSECTION,
            scan_ring_self_intersections,
            self._build_self_intersection_cfg,
        )

    def _on_fix_self_intersection(self) -> None:
        """Handle Section 1 Fix It click."""
        self._execute_fix(
            "1. Ring Self-Intersection",
            ERROR_SELF_INTERSECTION,
            fix_ring_self_intersections,
            self._build_self_intersection_cfg,
        )

    def _on_scan_overlap(self) -> None:
        """Handle Section 2 Scan click."""
        self._execute_scan(
            "2. Overlap Polygon",
            ERROR_OVERLAP,
            scan_overlaps,
            self._build_overlap_cfg,
        )

    def _on_fix_overlap(self) -> None:
        """Handle Section 2 Fix It click."""
        self._execute_fix(
            "2. Overlap Polygon",
            ERROR_OVERLAP,
            fix_overlaps,
            self._build_overlap_cfg,
        )

    def _on_scan_gap(self) -> None:
        """Handle Section 3 Scan click."""
        self._execute_scan(
            "3. Internal Gap",
            ERROR_GAP,
            scan_internal_gaps,
            self._build_gap_cfg,
        )

    def _on_fix_gap(self) -> None:
        """Handle Section 3 Fix It click."""
        self._execute_fix(
            "3. Internal Gap",
            ERROR_GAP,
            fix_internal_gaps,
            self._build_gap_cfg,
        )

    def _on_scan_sliver(self) -> None:
        """Handle Section 4 Scan click."""
        self._execute_scan(
            "4. Sliver Polygon",
            ERROR_SLIVER,
            scan_slivers,
            self._build_sliver_cfg,
        )

    def _on_fix_sliver(self) -> None:
        """Handle Section 4 Fix It click."""
        self._execute_fix(
            "4. Sliver Polygon",
            ERROR_SLIVER,
            fix_slivers,
            self._build_sliver_cfg,
        )


__all__ = ["OfficeBoyDialog", "PARAMETER_HELP_DOCS"]
