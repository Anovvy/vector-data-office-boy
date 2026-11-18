"""Tests for symbology constants, plugin metadata, Qt Designer UI XML, and PyQGIS integration."""

from __future__ import annotations

import configparser
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator

import pytest

from tests.conftest import REPO_ROOT
from vector_data_office_boy import classFactory
from vector_data_office_boy.core import (
    ERROR_COLORS,
    ERROR_GAP,
    ERROR_LAYER_NAMES,
    ERROR_OVERLAP,
    ERROR_SELF_INTERSECTION,
    ERROR_SLIVER,
    SCAN_ATTRIBUTE_FIELDS,
    ScanResult,
    TopologyErrorRecord,
)
from vector_data_office_boy.core.geom_backend import build_marker_polygon
from vector_data_office_boy.core.symbology import (
    HAS_QGIS,
    apply_single_error_symbology,
    create_temporary_error_layer,
)

PLUGIN_DIR = REPO_ROOT / "vector_data_office_boy"
METADATA_PATH = PLUGIN_DIR / "metadata.txt"
UI_PATH = PLUGIN_DIR / "ui" / "office_boy_dialog_base.ui"

EMOJI_PATTERN = re.compile(
    "["
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa70-\U0001faff"
    "\u2600-\u26ff"
    "\u2700-\u27bf"
    "]+",
    flags=re.UNICODE,
)

REQUIRED_UI_OBJECT_NAMES: tuple[str, ...] = (
    "tabMain",
    "tabParameters",
    "tabLog",
    "cmbInputLayer",
    "btnRefreshLayers",
    "chkRecalcAttrs",
    "chkPruneSpikes",
    "spinSpikeAngle",
    "btnScanSelfIntersect",
    "btnFixSelfIntersect",
    "cmbOverlapRule",
    "spinMinOverlapArea",
    "chkIncludeMicroOverlap",
    "btnScanOverlap",
    "btnFixOverlap",
    "cmbGapRule",
    "btnScanGap",
    "btnFixGap",
    "spinSliverArea",
    "spinSliverPP",
    "spinSliverMaxAreaPP",
    "spinPartMinArea",
    "cmbSliverRule",
    "btnScanSliver",
    "btnFixSliver",
    "txtParameterHelp",
    "tblSummary",
    "txtLog",
    "progressBar",
    "btnClose",
)


@pytest.fixture(scope="module")
def qgis_app() -> Iterator[object]:
    """Initialize a headless QgsApplication once per module when PyQGIS is available."""
    if not HAS_QGIS:
        yield None
        return

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.core import QgsApplication

    existing = QgsApplication.instance()
    if existing is not None:
        yield existing
        return

    app = QgsApplication([], False)
    app.initQgis()
    yield app
    app.exitQgis()


def test_error_colors_and_layer_names_contract() -> None:
    """Verify exact color codes, stroke widths, and layer names for all 4 sections."""
    assert ERROR_COLORS[ERROR_SELF_INTERSECTION]["fill"] == "#FF8C00"
    assert ERROR_COLORS[ERROR_OVERLAP]["fill"] == "#FF2400"
    assert ERROR_COLORS[ERROR_GAP]["fill"] == "#9B30FF"
    assert ERROR_COLORS[ERROR_SLIVER]["fill"] == "#FFD700"

    for error_type, style in ERROR_COLORS.items():
        assert style["stroke"] == style["fill"], (
            f"Stroke color must match fill color for {error_type}"
        )

    assert ERROR_COLORS[ERROR_SELF_INTERSECTION]["stroke_width_mm"] == 5.0
    assert ERROR_COLORS[ERROR_OVERLAP]["stroke_width_mm"] == 1.5
    assert ERROR_COLORS[ERROR_GAP]["stroke_width_mm"] == 5.0
    assert ERROR_COLORS[ERROR_SLIVER]["stroke_width_mm"] == 5.0

    assert ERROR_LAYER_NAMES == {
        ERROR_SELF_INTERSECTION: "Error_Ring_Self_Intersection",
        ERROR_OVERLAP: "Error_Overlap_Polygon",
        ERROR_GAP: "Error_Internal_Gap",
        ERROR_SLIVER: "Error_Sliver_Polygon",
    }


def test_metadata_txt_required_keys() -> None:
    """Verify metadata.txt adheres to QGIS Plugin Repository specifications."""
    assert METADATA_PATH.is_file()
    parser = configparser.RawConfigParser()
    parser.optionxform = str
    parser.read(METADATA_PATH, encoding="utf-8")
    assert parser.has_section("general")
    general = dict(parser.items("general"))

    assert general["name"] == "Vector Data Office Boy"
    assert general["version"] == "1.0.3"
    assert general["qgisMinimumVersion"] == "3.22"
    assert general["author"] == "Bayu Nabiil"
    assert general["email"] == "bayunabiil1365@gmail.com"
    assert general["license"] == "GPL-2.0-or-later"
    assert general["icon"] == "resources/icon.png"
    assert general["hasProcessingProvider"] == "yes"
    assert general["supportsQt6"] == "True"
    assert (PLUGIN_DIR / general["icon"]).is_file()
    assert (PLUGIN_DIR / "LICENSE").is_file()


def test_qt6_scoped_enums_compliance() -> None:
    """Verify plugin Python modules use Qt6-compatible scoped Qt and QGIS enums."""
    unscoped_patterns = (
        r"\bQt\.Window\b",
        r"\bQt\.Align(?:Right|Left|Center|Top|Bottom|VCenter|HCenter)\b",
        r"\bQEvent\.(?:FocusIn|Enter|MouseButtonPress)\b",
        r"\bQHeaderView\.(?:ResizeToContents|Stretch)\b",
        r"\bQgsWkbTypes\.(?:PolygonGeometry|MultiPolygon)\b",
        r"\bQgsProcessing\.TypeVectorPolygon\b",
        r"\bQgsProcessingAlgorithm\.FlagNoThreading\b",
        r"\bQgsProcessingParameterNumber\.Double\b",
        r"\bQVariant\.Type\.\w+\b",
    )
    compiled = [re.compile(pattern) for pattern in unscoped_patterns]
    for py_path in sorted(PLUGIN_DIR.rglob("*.py")):
        if "__pycache__" in py_path.parts:
            continue
        source = py_path.read_text(encoding="utf-8")
        for regex in compiled:
            match = regex.search(source)
            assert match is None, (
                f"Unscoped Qt5/QGIS enum '{match.group(0)}' found in {py_path.relative_to(REPO_ROOT)}"
            )


def test_zero_emoji_compliance_in_plugin_and_readme() -> None:
    """Enforce zero-emoji policy across README.md and all plugin source/UI files."""
    readme_path = REPO_ROOT / "README.md"
    assert readme_path.is_file()
    readme_text = readme_path.read_text(encoding="utf-8")
    assert not EMOJI_PATTERN.search(readme_text), "Emoji detected inside README.md"

    text_extensions = {".py", ".ui", ".txt", ".md"}
    for path in sorted(PLUGIN_DIR.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() not in text_extensions:
            continue
        content = path.read_text(encoding="utf-8")
        assert not EMOJI_PATTERN.search(content), f"Emoji detected inside {path}"


def test_qt_designer_ui_xml_uses_standard_widgets_and_keeps_object_names() -> None:
    """Verify .ui XML contains zero custom QGIS widgets and retains all expected objectNames."""
    assert UI_PATH.is_file()
    tree = ET.parse(UI_PATH)
    root = tree.getroot()

    assert root.findall(".//customwidgets") == [], "Found forbidden <customwidgets> section in .ui"
    assert root.findall(".//customwidget") == [], "Found forbidden <customwidget> element in .ui"

    widget_classes = {elem.attrib.get("class", "") for elem in root.findall(".//widget")}
    qgs_classes = sorted(cls for cls in widget_classes if cls.startswith("Qgs"))
    assert qgs_classes == [], f"Found forbidden QGIS custom widget classes in .ui: {qgs_classes}"

    object_names = {
        elem.attrib["name"]
        for elem in root.findall(".//widget")
        if "name" in elem.attrib
    }
    missing = [name for name in REQUIRED_UI_OBJECT_NAMES if name not in object_names]
    assert missing == [], f"Missing required widget objectName(s) in .ui: {missing}"


@pytest.mark.skipif(not HAS_QGIS, reason="PyQGIS is not available in this Python environment.")
@pytest.mark.parametrize(
    ("error_type", "expected_hex"),
    [
        (ERROR_SELF_INTERSECTION, "#ff8c00"),
        (ERROR_OVERLAP, "#ff2400"),
        (ERROR_GAP, "#9b30ff"),
        (ERROR_SLIVER, "#ffd700"),
    ],
)
def test_pyqgis_temporary_error_layer_and_single_symbol_renderer(
    qgis_app: object,
    error_type: str,
    expected_hex: str,
) -> None:
    """Verify PyQGIS temporary memory layer creation, 11-column schema, and fill colors."""
    _ = qgis_app
    from qgis.core import QgsCoordinateReferenceSystem, QgsSingleSymbolRenderer

    crs_wkt = QgsCoordinateReferenceSystem("EPSG:32748").toWkt()
    marker = build_marker_polygon(680000.0, 9300000.0, radius_m=5.0)
    record = TopologyErrorRecord(
        error_id=1,
        error_type=error_type,
        severity="HIGH",
        fid_1=10,
        fid_2="11",
        oid_ref="10,11",
        area_m2=float(marker.area),
        perim_m=float(marker.length),
        pp_index=0.99,
        error_x=680000.0,
        error_y=9300000.0,
        geometry_wkb=marker.wkb,
    )
    scan_res = ScanResult(
        error_type=error_type,
        layer_name=ERROR_LAYER_NAMES[error_type],
        crs_wkt=crs_wkt,
        error_count=1,
        total_area_m2=float(marker.area),
        records=[record],
        logs=["Unit test scan result."],
    )

    layer = create_temporary_error_layer(scan_res)
    assert layer.isValid()
    assert layer.providerType() == "memory"
    assert layer.name() == ERROR_LAYER_NAMES[error_type]
    assert tuple(f.name() for f in layer.fields()) == SCAN_ATTRIBUTE_FIELDS
    assert layer.featureCount() == 1

    apply_single_error_symbology(layer, error_type)
    renderer = layer.renderer()
    assert isinstance(renderer, QgsSingleSymbolRenderer)
    symbol = renderer.symbol()
    assert symbol.color().name().lower() == expected_hex
    symbol_layer = symbol.symbolLayer(0)
    assert symbol_layer.strokeColor().name().lower() == expected_hex
    assert symbol_layer.strokeWidth() == pytest.approx(
        float(ERROR_COLORS[error_type]["stroke_width_mm"])
    )


@pytest.mark.skipif(not HAS_QGIS, reason="PyQGIS is not available in this Python environment.")
def test_pyqgis_dialog_provider_and_class_factory(qgis_app: object) -> None:
    """Verify classFactory, OfficeBoyDialog, PARAMETER_HELP_DOCS, and Processing provider."""
    _ = qgis_app
    from qgis.core import QgsApplication

    from vector_data_office_boy.processing_provider import VectorDataOfficeBoyProvider
    from vector_data_office_boy.ui.office_boy_dialog import PARAMETER_HELP_DOCS, OfficeBoyDialog

    plugin = classFactory(None)
    dialog = None
    try:
        plugin.initProcessing()
        registry = QgsApplication.processingRegistry()
        provider = (
            registry.providerById("vector_data_office_boy")
            if registry is not None
            else None
        )
        if provider is None:
            provider = VectorDataOfficeBoyProvider()
            provider.loadAlgorithms()

        alg_names = {alg.name() for alg in provider.algorithms()}
        assert alg_names == {
            "scan_ring_self_intersections",
            "scan_overlaps",
            "scan_internal_gaps",
            "scan_slivers",
            "fix_ring_self_intersections",
            "fix_overlaps",
            "fix_internal_gaps",
            "fix_slivers",
        }

        dialog = OfficeBoyDialog(iface=None)
        for widget_name in REQUIRED_UI_OBJECT_NAMES:
            assert hasattr(dialog, widget_name), f"Dialog missing {widget_name}"

        for key in set(dialog._help_widget_map.values()):
            assert key in PARAMETER_HELP_DOCS, f"Missing help doc for {key}"
            dialog._show_help_for(key)
            assert len(dialog.txtParameterHelp.toPlainText().strip()) > 20
    finally:
        if dialog is not None:
            dialog.cleanup()
            dialog.close()
            dialog.deleteLater()
        plugin.unload()
