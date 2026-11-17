"""Dual-mode vector loader and atomic overwrite writer for files and live QGIS layers."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from vector_data_office_boy.core.geom_backend import MetricCrsContext, restore_z, to_2d

logger = logging.getLogger(__name__)

try:
    import geopandas as gpd

    HAS_GEOPANDAS = True
except ImportError:
    gpd = None  # type: ignore[assignment]
    HAS_GEOPANDAS = False

try:
    import pyogrio

    HAS_PYOGRIO = True
except ImportError:
    pyogrio = None  # type: ignore[assignment]
    HAS_PYOGRIO = False

try:
    from qgis.core import NULL as QGS_NULL
    from qgis.core import QgsFeatureRequest, QgsGeometry, QgsVectorLayer, QgsWkbTypes

    HAS_QGIS = True
except ImportError:
    QGS_NULL = None  # type: ignore[assignment]
    QgsFeatureRequest = None  # type: ignore[assignment,misc]
    QgsGeometry = None  # type: ignore[assignment,misc]
    QgsVectorLayer = None  # type: ignore[assignment,misc]
    QgsWkbTypes = None  # type: ignore[assignment,misc]
    HAS_QGIS = False

_DRIVER_BY_SUFFIX: dict[str, str] = {
    ".shp": "ESRI Shapefile",
    ".gpkg": "GPKG",
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".fgb": "FlatGeobuf",
}

_STALE_SHP_INDEX_SUFFIXES: tuple[str, ...] = (".sbn", ".sbx", ".qix", ".aih", ".ain")


@dataclass
class LoadedLayerData:
    """In-memory normalized representation of an input polygon vector layer."""

    source: Any
    is_qgis_layer: bool
    source_path: str
    layer_name: str
    crs_wkt: str
    driver: str
    encoding: str
    has_z: bool
    fids: list[int]
    field_names: list[str]
    attributes: list[dict[str, Any]]
    geometries_orig: list[BaseGeometry]
    geometries_2d: list[BaseGeometry]
    gdf: Any = None


def _is_qgs_vector_layer(source: Any) -> bool:
    if isinstance(source, (str, Path)):
        return False
    if HAS_QGIS and QgsVectorLayer is not None and isinstance(source, QgsVectorLayer):
        return True
    return (
        hasattr(source, "dataProvider")
        and hasattr(source, "getFeatures")
        and hasattr(source, "fields")
    )


def _detect_shapefile_encoding(path: Path) -> str:
    cpg_path = path.with_suffix(".cpg")
    if cpg_path.exists():
        text = cpg_path.read_text(encoding="ascii", errors="ignore").strip()
        if text:
            return text
    return "UTF-8"


def _load_from_qgs_layer(layer: Any) -> LoadedLayerData:
    fields = layer.fields()
    field_names = [str(f.name()) for f in fields]
    fids: list[int] = []
    attributes: list[dict[str, Any]] = []
    geometries_orig: list[BaseGeometry] = []
    geometries_2d: list[BaseGeometry] = []

    req = QgsFeatureRequest() if (HAS_QGIS and QgsFeatureRequest is not None) else None
    feature_iter = layer.getFeatures(req) if req is not None else layer.getFeatures()

    for feat in feature_iter:
        fids.append(int(feat.id()))
        attrs = feat.attributes()
        row_dict: dict[str, Any] = {}
        for name, val in zip(field_names, attrs, strict=False):
            is_null = (
                val is None
                or (QGS_NULL is not None and val == QGS_NULL)
                or (hasattr(val, "isNull") and callable(val.isNull) and val.isNull())
            )
            row_dict[name] = None if is_null else val
        attributes.append(row_dict)
        qgs_geom = feat.geometry()
        if qgs_geom is None or qgs_geom.isNull() or qgs_geom.isEmpty():
            geom = Polygon()
        else:
            wkb_bytes = bytes(qgs_geom.asWkb())
            geom = shapely.from_wkb(wkb_bytes) if wkb_bytes else Polygon()
        geometries_orig.append(geom)
        geometries_2d.append(to_2d(geom))

    has_z = any(g is not None and not g.is_empty and g.has_z for g in geometries_orig)
    if not has_z and HAS_QGIS and QgsWkbTypes is not None and hasattr(layer, "wkbType"):
        has_z = bool(QgsWkbTypes.hasZ(layer.wkbType()))

    crs_wkt = ""
    if hasattr(layer, "crs") and layer.crs() is not None:
        crs_wkt = str(layer.crs().toWkt() or "")

    raw_src = ""
    if hasattr(layer, "source"):
        raw_src = str(layer.source() or "")
    source_path = raw_src.split("|")[0] if raw_src else ""

    storage_type = ""
    if hasattr(layer, "dataProvider") and layer.dataProvider() is not None:
        provider = layer.dataProvider()
        if hasattr(provider, "storageType"):
            storage_type = str(provider.storageType() or "")

    layer_name = str(layer.name()) if hasattr(layer, "name") else "layer"

    return LoadedLayerData(
        source=layer,
        is_qgis_layer=True,
        source_path=source_path,
        layer_name=layer_name,
        crs_wkt=crs_wkt,
        driver=storage_type,
        encoding="UTF-8",
        has_z=has_z,
        fids=fids,
        field_names=field_names,
        attributes=attributes,
        geometries_orig=geometries_orig,
        geometries_2d=geometries_2d,
        gdf=None,
    )


def _load_from_file_path(path_input: str | Path) -> LoadedLayerData:
    target = Path(path_input).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Input vector file does not exist: {target}")

    if not HAS_GEOPANDAS or gpd is None:
        if HAS_QGIS and QgsVectorLayer is not None:
            qgs_layer = QgsVectorLayer(str(target), target.stem, "ogr")
            if not qgs_layer.isValid():
                raise RuntimeError(f"Unable to open vector file with QGIS OGR: {target}")
            loaded = _load_from_qgs_layer(qgs_layer)
            loaded.source_path = str(target)
            return loaded
        raise ImportError("geopandas is required to read vector file paths outside QGIS.")

    driver = _DRIVER_BY_SUFFIX.get(target.suffix.lower(), "")
    layer_name = target.stem
    encoding = _detect_shapefile_encoding(target) if target.suffix.lower() == ".shp" else "UTF-8"

    if HAS_PYOGRIO and pyogrio is not None:
        try:
            info = pyogrio.read_info(target)
            driver = str(info.get("driver") or driver)
            layer_name = str(info.get("layer_name") or layer_name)
            encoding = str(info.get("encoding") or encoding)
        except Exception as exc:
            logger.debug("Falling back from pyogrio.read_info for %s: %s", target, exc)

    gdf = gpd.read_file(target)
    geom_col = gdf.geometry.name
    field_names = [str(col) for col in gdf.columns if col != geom_col]

    fids: list[int] = []
    attributes: list[dict[str, Any]] = []
    geometries_orig: list[BaseGeometry] = []
    geometries_2d: list[BaseGeometry] = []

    records = gdf[field_names].to_dict(orient="records") if field_names else [{} for _ in range(len(gdf))]
    for idx, (row_attrs, geom) in enumerate(zip(records, gdf[geom_col], strict=False)):
        fids.append(idx)
        attributes.append(row_attrs)
        clean_geom = geom if geom is not None else Polygon()
        geometries_orig.append(clean_geom)
        geometries_2d.append(to_2d(clean_geom))

    has_z = any(g is not None and not g.is_empty and g.has_z for g in geometries_orig)
    crs_wkt = str(gdf.crs.to_wkt()) if gdf.crs is not None else ""

    return LoadedLayerData(
        source=target,
        is_qgis_layer=False,
        source_path=str(target),
        layer_name=layer_name,
        crs_wkt=crs_wkt,
        driver=driver or "ESRI Shapefile",
        encoding=encoding or "UTF-8",
        has_z=has_z,
        fids=fids,
        field_names=field_names,
        attributes=attributes,
        geometries_orig=geometries_orig,
        geometries_2d=geometries_2d,
        gdf=gdf,
    )


def load_vector_source(source: str | Path | Any) -> LoadedLayerData:
    """Load features and metadata cleanly from a file path or QgsVectorLayer."""
    if _is_qgs_vector_layer(source):
        return _load_from_qgs_layer(source)
    if isinstance(source, (str, Path)):
        return _load_from_file_path(source)
    raise TypeError(
        f"Unsupported vector source type '{type(source).__name__}'. "
        "Expected str, pathlib.Path, or QgsVectorLayer."
    )


def _compute_recalc_values(area_m2: float, perim_m: float) -> dict[str, float]:
    return {
        "LUAS_HA": round(float(area_m2) / 10000.0, 4),
        "Shape_Area": float(area_m2),
        "Shape_Le_1": float(perim_m),
    }


def _overwrite_qgs_layer(
    loaded: LoadedLayerData,
    new_geometries_source: list[BaseGeometry],
    metric_areas_perims: list[tuple[float, float]] | None,
    recalc_attributes: bool,
) -> tuple[str, int]:
    layer = loaded.source
    provider = layer.dataProvider()
    fields = layer.fields()

    idx_luas = fields.indexFromName("LUAS_HA")
    idx_area = fields.indexFromName("Shape_Area")
    idx_perim = fields.indexFromName("Shape_Le_1")

    geom_changes: dict[int, Any] = {}
    attr_changes: dict[int, dict[int, Any]] = {}
    delete_fids: list[int] = []
    surviving = 0

    for idx, fid in enumerate(loaded.fids):
        new_geom = new_geometries_source[idx]
        if new_geom is None or new_geom.is_empty:
            delete_fids.append(fid)
            continue
        surviving += 1
        orig_geom = loaded.geometries_orig[idx]
        changed = (
            orig_geom is None
            or orig_geom.is_empty
            or new_geom.has_z != orig_geom.has_z
            or new_geom.wkb != orig_geom.wkb
        )
        if changed:
            qgs_geom = QgsGeometry()
            qgs_geom.fromWkb(new_geom.wkb)
            geom_changes[fid] = qgs_geom

        if recalc_attributes and (idx_luas >= 0 or idx_area >= 0 or idx_perim >= 0):
            if metric_areas_perims is not None:
                area_m2, perim_m = metric_areas_perims[idx]
            else:
                g2d = to_2d(new_geom)
                area_m2, perim_m = float(g2d.area), float(g2d.length)
            recalc = _compute_recalc_values(area_m2, perim_m)
            row_attrs = loaded.attributes[idx]
            fid_attrs: dict[int, Any] = {}
            for field_name, field_idx in (
                ("LUAS_HA", idx_luas),
                ("Shape_Area", idx_area),
                ("Shape_Le_1", idx_perim),
            ):
                if field_idx < 0:
                    continue
                new_val = recalc[field_name]
                old_val = row_attrs.get(field_name)
                try:
                    differs = old_val is None or abs(float(old_val) - new_val) > 1e-6
                except (TypeError, ValueError):
                    differs = True
                if changed or differs:
                    fid_attrs[field_idx] = new_val
            if fid_attrs:
                attr_changes[fid] = fid_attrs

    was_editable = bool(hasattr(layer, "isEditable") and layer.isEditable())
    if was_editable:
        for fid, qgs_geom in geom_changes.items():
            layer.changeGeometry(fid, qgs_geom)
        for fid, fid_attrs in attr_changes.items():
            for attr_idx, val in fid_attrs.items():
                layer.changeAttributeValue(fid, attr_idx, val)
        for fid in delete_fids:
            layer.deleteFeature(fid)
        if not layer.commitChanges():
            raise RuntimeError("Failed to commit topology edits on active QGIS edit session.")
    else:
        if geom_changes and not provider.changeGeometryValues(geom_changes):
            raise RuntimeError("QgsVectorDataProvider.changeGeometryValues failed.")
        if attr_changes and not provider.changeAttributeValues(attr_changes):
            raise RuntimeError("QgsVectorDataProvider.changeAttributeValues failed.")
        if delete_fids and not provider.deleteFeatures(delete_fids):
            raise RuntimeError("QgsVectorDataProvider.deleteFeatures failed.")

    layer.updateExtents()
    provider.forceReload()
    layer.triggerRepaint()
    return loaded.source_path or loaded.layer_name, surviving


def _overwrite_file_path(
    loaded: LoadedLayerData,
    new_geometries_source: list[BaseGeometry],
    metric_areas_perims: list[tuple[float, float]] | None,
    recalc_attributes: bool,
) -> tuple[str, int]:
    if not HAS_GEOPANDAS or gpd is None or loaded.gdf is None:
        raise RuntimeError("geopandas GeoDataFrame context is required for file overwrite.")

    target_path = Path(loaded.source_path).resolve()
    gdf = loaded.gdf
    geom_col = gdf.geometry.name

    keep_indices: list[int] = []
    surviving_geoms: list[BaseGeometry] = []
    luas_vals: list[float] = []
    area_vals: list[float] = []
    perim_vals: list[float] = []

    for idx, new_geom in enumerate(new_geometries_source):
        if new_geom is None or new_geom.is_empty:
            continue
        keep_indices.append(idx)
        surviving_geoms.append(new_geom)
        if recalc_attributes:
            if metric_areas_perims is not None:
                area_m2, perim_m = metric_areas_perims[idx]
            else:
                g2d = to_2d(new_geom)
                area_m2, perim_m = float(g2d.area), float(g2d.length)
            vals = _compute_recalc_values(area_m2, perim_m)
            luas_vals.append(vals["LUAS_HA"])
            area_vals.append(vals["Shape_Area"])
            perim_vals.append(vals["Shape_Le_1"])

    out_gdf = gdf.iloc[keep_indices].copy().reset_index(drop=True)
    out_gdf[geom_col] = gpd.GeoSeries(surviving_geoms, index=out_gdf.index, crs=gdf.crs)
    out_gdf = out_gdf.set_geometry(geom_col)

    if recalc_attributes:
        if "LUAS_HA" in out_gdf.columns:
            out_gdf["LUAS_HA"] = luas_vals
        if "Shape_Area" in out_gdf.columns:
            out_gdf["Shape_Area"] = area_vals
        if "Shape_Le_1" in out_gdf.columns:
            out_gdf["Shape_Le_1"] = perim_vals

    suffix = target_path.suffix.lower()
    driver = loaded.driver or _DRIVER_BY_SUFFIX.get(suffix, "ESRI Shapefile")

    with tempfile.TemporaryDirectory(dir=str(target_path.parent), prefix=".vdob_tmp_") as tmp_str:
        tmp_dir = Path(tmp_str)
        staged_path = tmp_dir / target_path.name

        if driver == "GPKG" or suffix == ".gpkg":
            out_gdf.to_file(staged_path, driver="GPKG", layer=loaded.layer_name)
        elif driver == "ESRI Shapefile" or suffix == ".shp":
            out_gdf.to_file(
                staged_path,
                driver="ESRI Shapefile",
                encoding=loaded.encoding or "UTF-8",
            )
        else:
            out_gdf.to_file(staged_path, driver=driver)

        if driver == "ESRI Shapefile" or suffix == ".shp":
            stem = target_path.stem
            for stale_ext in _STALE_SHP_INDEX_SUFFIXES:
                stale_file = target_path.parent / f"{stem}{stale_ext}"
                if stale_file.exists():
                    stale_file.unlink()
            for staged_sidecar in tmp_dir.glob(f"{stem}.*"):
                dest_sidecar = target_path.parent / staged_sidecar.name
                os.replace(staged_sidecar, dest_sidecar)
        else:
            if suffix == ".gpkg":
                for wal_tail in ("-wal", "-shm"):
                    wal_file = target_path.with_name(f"{target_path.name}{wal_tail}")
                    if wal_file.exists():
                        wal_file.unlink()
            os.replace(staged_path, target_path)

    return str(target_path), len(out_gdf)


def atomic_overwrite_vector_source(
    loaded: LoadedLayerData,
    new_geometries_source: list[BaseGeometry],
    metric_areas_perims: list[tuple[float, float]] | None = None,
    recalc_attributes: bool = True,
) -> tuple[str, int]:
    """Atomically overwrite the input vector dataset or update a live QgsVectorLayer."""
    if len(new_geometries_source) != len(loaded.fids):
        raise ValueError(
            "Length of new_geometries_source must match loaded feature count."
        )
    if loaded.is_qgis_layer:
        return _overwrite_qgs_layer(
            loaded=loaded,
            new_geometries_source=new_geometries_source,
            metric_areas_perims=metric_areas_perims,
            recalc_attributes=recalc_attributes,
        )
    return _overwrite_file_path(
        loaded=loaded,
        new_geometries_source=new_geometries_source,
        metric_areas_perims=metric_areas_perims,
        recalc_attributes=recalc_attributes,
    )


def commit_metric_geometries(
    loaded: LoadedLayerData,
    ctx: MetricCrsContext,
    orig_metric_geoms: list[BaseGeometry],
    fixed_metric_geoms: list[BaseGeometry],
    recalc_attributes: bool = True,
    modified_flags: list[bool] | None = None,
) -> tuple[str, int, float]:
    """Convert metric fixed geometries back to source CRS/Z and overwrite source."""
    source_geoms: list[BaseGeometry] = []
    areas_perims: list[tuple[float, float]] = []
    area_changed_m2 = 0.0
    any_changed = False

    for idx, (orig_m, fixed_m) in enumerate(zip(orig_metric_geoms, fixed_metric_geoms, strict=False)):
        orig_area = float(orig_m.area) if orig_m is not None and not orig_m.is_empty else 0.0
        new_area = float(fixed_m.area) if fixed_m is not None and not fixed_m.is_empty else 0.0
        new_perim = float(fixed_m.length) if fixed_m is not None and not fixed_m.is_empty else 0.0
        areas_perims.append((new_area, new_perim))
        area_changed_m2 += abs(new_area - orig_area)

        if modified_flags is not None:
            is_mod = modified_flags[idx]
        else:
            is_mod = (
                fixed_m is None
                or orig_m is None
                or fixed_m.is_empty != orig_m.is_empty
                or (not orig_m.is_empty and not orig_m.is_valid)
                or (not fixed_m.is_empty and not fixed_m.equals_exact(orig_m, 1e-12))
            )

        if is_mod:
            any_changed = True
            if fixed_m is None or fixed_m.is_empty:
                source_geoms.append(Polygon())
            else:
                src_2d = ctx.to_source(fixed_m)
                source_geoms.append(
                    restore_z(
                        src_2d,
                        has_z=loaded.has_z,
                        reference_geom=loaded.geometries_orig[idx],
                    )
                )
        else:
            source_geoms.append(loaded.geometries_orig[idx])

    if not any_changed:
        return loaded.source_path or loaded.layer_name, len(loaded.fids), 0.0

    out_path, features_after = atomic_overwrite_vector_source(
        loaded=loaded,
        new_geometries_source=source_geoms,
        metric_areas_perims=areas_perims,
        recalc_attributes=recalc_attributes,
    )
    return out_path, features_after, float(area_changed_m2)
