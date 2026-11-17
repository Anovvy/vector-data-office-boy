"""PyQGIS temporary memory layer creation and single-symbol error styling."""

from __future__ import annotations

from typing import Any

from vector_data_office_boy.core.models import (
    ERROR_COLORS,
    SCAN_ATTRIBUTE_FIELDS,
    ScanResult,
)

try:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsField,
        QgsFillSymbol,
        QgsGeometry,
        QgsProject,
        QgsSingleSymbolRenderer,
        QgsVectorLayer,
    )
    from qgis.PyQt.QtCore import QMetaType, QVariant

    HAS_QGIS = True
except ImportError:
    QgsCoordinateReferenceSystem = None  # type: ignore[assignment,misc]
    QgsFeature = None  # type: ignore[assignment,misc]
    QgsField = None  # type: ignore[assignment,misc]
    QgsFillSymbol = None  # type: ignore[assignment,misc]
    QgsGeometry = None  # type: ignore[assignment,misc]
    QgsProject = None  # type: ignore[assignment,misc]
    QgsSingleSymbolRenderer = None  # type: ignore[assignment,misc]
    QgsVectorLayer = None  # type: ignore[assignment,misc]
    QMetaType = None  # type: ignore[assignment,misc]
    QVariant = None  # type: ignore[assignment,misc]
    HAS_QGIS = False

_FIELD_KINDS: dict[str, str] = {
    "error_id": "int",
    "error_type": "string",
    "severity": "string",
    "fid_1": "int",
    "fid_2": "string",
    "oid_ref": "string",
    "area_m2": "double",
    "perim_m": "double",
    "pp_index": "double",
    "error_x": "double",
    "error_y": "double",
}


def _require_qgis() -> None:
    if not HAS_QGIS:
        raise RuntimeError(
            "PyQGIS (qgis.core / qgis.PyQt) is required to create or style QGIS map layers."
        )


def _make_qgs_field(name: str, kind: str) -> Any:
    if QMetaType is not None and hasattr(QMetaType, "Type"):
        qmeta_map = {
            "int": QMetaType.Type.LongLong,
            "double": QMetaType.Type.Double,
            "string": QMetaType.Type.QString,
        }
        try:
            return QgsField(name, qmeta_map[kind])
        except TypeError:
            pass
    qvar_map = {
        "int": QVariant.LongLong,
        "double": QVariant.Double,
        "string": QVariant.String,
    }
    return QgsField(name, qvar_map[kind])


def _resolve_crs(crs_wkt: str) -> Any:
    crs = QgsCoordinateReferenceSystem()
    clean_wkt = (crs_wkt or "").strip()
    if clean_wkt:
        if not crs.createFromWkt(clean_wkt):
            crs.createFromUserInput(clean_wkt)
    return crs


def apply_single_error_symbology(layer: Any, error_type: str) -> None:
    """Apply a single-symbol fill style matching ERROR_COLORS[error_type]."""
    _require_qgis()
    if error_type not in ERROR_COLORS:
        raise ValueError(f"Unsupported error_type '{error_type}' for symbology.")

    style = ERROR_COLORS[error_type]
    symbol = QgsFillSymbol.createSimple(
        {
            "color": str(style["fill"]),
            "outline_color": str(style["stroke"]),
            "outline_width": "0.6",
            "style": "solid",
            "outline_style": "solid",
        }
    )
    symbol.setOpacity(float(style["opacity"]))
    renderer = QgsSingleSymbolRenderer(symbol)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def create_temporary_error_layer(scan_result: ScanResult) -> Any:
    """Build an in-memory polygon QgsVectorLayer containing the 11-column scan schema."""
    _require_qgis()
    crs = _resolve_crs(scan_result.crs_wkt)
    authid = str(crs.authid() or "") if crs.isValid() else ""
    uri = f"Polygon?crs={authid}" if authid else "Polygon"

    layer = QgsVectorLayer(uri, scan_result.layer_name, "memory")
    if crs.isValid():
        layer.setCrs(crs)

    provider = layer.dataProvider()
    fields = [_make_qgs_field(name, _FIELD_KINDS[name]) for name in SCAN_ATTRIBUTE_FIELDS]
    provider.addAttributes(fields)
    layer.updateFields()

    qgs_fields = layer.fields()
    features: list[Any] = []
    for record in scan_result.records:
        feat = QgsFeature(qgs_fields)
        qgs_geom = QgsGeometry()
        if record.geometry_wkb:
            qgs_geom.fromWkb(record.geometry_wkb)
        feat.setGeometry(qgs_geom)
        feat.setAttributes(
            [
                int(record.error_id),
                str(record.error_type),
                str(record.severity),
                int(record.fid_1),
                str(record.fid_2),
                str(record.oid_ref),
                float(record.area_m2),
                float(record.perim_m),
                float(record.pp_index),
                float(record.error_x),
                float(record.error_y),
            ]
        )
        features.append(feat)

    if features:
        provider.addFeatures(features)

    layer.updateExtents()
    apply_single_error_symbology(layer, scan_result.error_type)
    return layer


def add_or_replace_error_layer_in_project(
    scan_result: ScanResult,
    project: Any = None,
    iface: Any = None,
) -> Any:
    """Replace any existing temporary error layer with the same name and style it."""
    _require_qgis()
    proj = project if project is not None else QgsProject.instance()
    existing_ids = [
        lyr.id()
        for lyr in proj.mapLayers().values()
        if lyr.name() == scan_result.layer_name and lyr.providerType() == "memory"
    ]
    if existing_ids:
        proj.removeMapLayers(existing_ids)

    layer = create_temporary_error_layer(scan_result)
    apply_single_error_symbology(layer, scan_result.error_type)
    proj.addMapLayer(layer)
    layer.triggerRepaint()

    if iface is not None:
        layer_tree = iface.layerTreeView() if hasattr(iface, "layerTreeView") else None
        if layer_tree is not None and hasattr(layer_tree, "refreshLayerSymbology"):
            layer_tree.refreshLayerSymbology(layer.id())
        canvas = iface.mapCanvas() if hasattr(iface, "mapCanvas") else None
        if canvas is not None and hasattr(canvas, "refresh"):
            canvas.refresh()

    return layer
