"""Geometry backend helpers, metric CRS normalization, and Z-dimension preservation."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import shapely
from shapely import ops
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

try:
    import pyproj

    HAS_PYPROJ = True
except ImportError:
    pyproj = None  # type: ignore[assignment]
    HAS_PYPROJ = False

try:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsPointXY,
        QgsProject,
    )

    HAS_QGIS = True
except ImportError:
    QgsCoordinateReferenceSystem = None  # type: ignore[assignment,misc]
    QgsCoordinateTransform = None  # type: ignore[assignment,misc]
    QgsPointXY = None  # type: ignore[assignment,misc]
    QgsProject = None  # type: ignore[assignment,misc]
    HAS_QGIS = False

_VALIDITY_COORD_RE = re.compile(
    r"\[\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)[,\s]+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*\]"
)

_OID_CANDIDATE_KEYS: tuple[str, ...] = (
    "OBJECTID",
    "objectid",
    "OID",
    "oid",
    "ID",
    "id",
    "FID",
    "fid",
)


def _identity_xy(x: Any, y: Any) -> tuple[Any, Any]:
    return x, y


@dataclass
class MetricCrsContext:
    """Bidirectional transformation bridge between layer CRS and metric planar CRS."""

    source_crs_wkt: str
    metric_crs_wkt: str
    is_geographic: bool
    was_reprojected: bool
    _forward_fn: Callable[[Any, Any], tuple[Any, Any]] = _identity_xy
    _inverse_fn: Callable[[Any, Any], tuple[Any, Any]] = _identity_xy

    def to_metric(self, geom: BaseGeometry) -> BaseGeometry:
        """Transform a 2D geometry from source CRS into metric planar CRS."""
        if not self.was_reprojected or geom is None or geom.is_empty:
            return geom
        return ops.transform(self._forward_fn, geom)

    def to_source(self, geom: BaseGeometry) -> BaseGeometry:
        """Transform a 2D geometry from metric planar CRS back to source CRS."""
        if not self.was_reprojected or geom is None or geom.is_empty:
            return geom
        return ops.transform(self._inverse_fn, geom)

    def transform_point_to_metric(self, x: float, y: float) -> tuple[float, float]:
        """Transform a single coordinate pair to metric CRS."""
        if not self.was_reprojected:
            return float(x), float(y)
        mx, my = self._forward_fn(float(x), float(y))
        return float(mx), float(my)

    def transform_point_to_source(self, x: float, y: float) -> tuple[float, float]:
        """Transform a single coordinate pair back to source CRS."""
        if not self.was_reprojected:
            return float(x), float(y)
        sx, sy = self._inverse_fn(float(x), float(y))
        return float(sx), float(sy)


def _utm_epsg_from_lon_lat(lon: float, lat: float) -> int:
    clamped_lon = max(-180.0, min(179.999999, float(lon)))
    clamped_lat = max(-80.0, min(84.0, float(lat)))
    zone = int((clamped_lon + 180.0) // 6.0) + 1
    zone = max(1, min(60, zone))
    return (32600 + zone) if clamped_lat >= 0.0 else (32700 + zone)


def _representative_lon_lat(
    geoms: list[BaseGeometry],
    to_wgs84: Callable[[Any, Any], tuple[Any, Any]] | None = None,
) -> tuple[float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for geom in geoms:
        if geom is None or geom.is_empty:
            continue
        minx, miny, maxx, maxy = geom.bounds
        if math.isfinite(minx) and math.isfinite(miny) and math.isfinite(maxx) and math.isfinite(maxy):
            xs.append(0.5 * (minx + maxx))
            ys.append(0.5 * (miny + maxy))
    if not xs:
        return 0.0, 0.0
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    if to_wgs84 is not None:
        lon, lat = to_wgs84(cx, cy)
        return float(lon), float(lat)
    return float(cx), float(cy)


def _is_projected_in_meters(crs: Any) -> bool:
    if not crs.is_projected:
        return False
    axis_info = getattr(crs, "axis_info", None)
    if not axis_info:
        return True
    factor = getattr(axis_info[0], "unit_conversion_factor", 1.0)
    return abs(float(factor) - 1.0) < 1e-6


def build_metric_context(crs_wkt: str, geoms: list[BaseGeometry]) -> MetricCrsContext:
    """Build a metric CRS context, projecting degree or non-meter layers to UTM."""
    clean_wkt = (crs_wkt or "").strip()
    if not clean_wkt:
        return MetricCrsContext(
            source_crs_wkt="",
            metric_crs_wkt="",
            is_geographic=False,
            was_reprojected=False,
        )

    if HAS_PYPROJ and pyproj is not None:
        try:
            src_crs = pyproj.CRS.from_user_input(clean_wkt)
        except Exception:
            src_crs = None
        if src_crs is not None:
            if _is_projected_in_meters(src_crs):
                return MetricCrsContext(
                    source_crs_wkt=clean_wkt,
                    metric_crs_wkt=clean_wkt,
                    is_geographic=False,
                    was_reprojected=False,
                )
            wgs84 = pyproj.CRS.from_epsg(4326)
            to_wgs_fn = None
            if not src_crs.is_geographic:
                to_wgs = pyproj.Transformer.from_crs(src_crs, wgs84, always_xy=True)
                to_wgs_fn = to_wgs.transform
            lon, lat = _representative_lon_lat(geoms, to_wgs_fn)
            utm_epsg = _utm_epsg_from_lon_lat(lon, lat)
            utm_crs = pyproj.CRS.from_epsg(utm_epsg)
            forward = pyproj.Transformer.from_crs(src_crs, utm_crs, always_xy=True)
            inverse = pyproj.Transformer.from_crs(utm_crs, src_crs, always_xy=True)
            return MetricCrsContext(
                source_crs_wkt=clean_wkt,
                metric_crs_wkt=utm_crs.to_wkt(),
                is_geographic=bool(src_crs.is_geographic),
                was_reprojected=True,
                _forward_fn=forward.transform,
                _inverse_fn=inverse.transform,
            )

    if HAS_QGIS and QgsCoordinateReferenceSystem is not None:
        src_qgis = QgsCoordinateReferenceSystem()
        if src_qgis.createFromWkt(clean_wkt) or src_qgis.createFromUserInput(clean_wkt):
            is_geo = bool(src_qgis.isGeographic())
            if not is_geo:
                return MetricCrsContext(
                    source_crs_wkt=clean_wkt,
                    metric_crs_wkt=clean_wkt,
                    is_geographic=False,
                    was_reprojected=False,
                )
            lon, lat = _representative_lon_lat(geoms)
            utm_epsg = _utm_epsg_from_lon_lat(lon, lat)
            utm_qgis = QgsCoordinateReferenceSystem.fromEpsgId(utm_epsg)
            ctx_inst = QgsProject.instance().transformContext()
            fwd_tx = QgsCoordinateTransform(src_qgis, utm_qgis, ctx_inst)
            inv_tx = QgsCoordinateTransform(utm_qgis, src_qgis, ctx_inst)

            def _qgs_transform(tx: Any, x: Any, y: Any) -> tuple[Any, Any]:
                if isinstance(x, (int, float)):
                    pt = tx.transform(QgsPointXY(float(x), float(y)))
                    return float(pt.x()), float(pt.y())
                out_x: list[float] = []
                out_y: list[float] = []
                for xi, yi in zip(x, y, strict=False):
                    pt = tx.transform(QgsPointXY(float(xi), float(yi)))
                    out_x.append(float(pt.x()))
                    out_y.append(float(pt.y()))
                return out_x, out_y

            return MetricCrsContext(
                source_crs_wkt=clean_wkt,
                metric_crs_wkt=utm_qgis.toWkt(),
                is_geographic=True,
                was_reprojected=True,
                _forward_fn=lambda x, y: _qgs_transform(fwd_tx, x, y),
                _inverse_fn=lambda x, y: _qgs_transform(inv_tx, x, y),
            )

    return MetricCrsContext(
        source_crs_wkt=clean_wkt,
        metric_crs_wkt=clean_wkt,
        is_geographic=False,
        was_reprojected=False,
    )


def to_2d(geom: BaseGeometry | None) -> BaseGeometry:
    """Strip any Z coordinates from a Shapely geometry."""
    if geom is None or geom.is_empty:
        return Polygon()
    if not geom.has_z:
        return geom
    if hasattr(shapely, "force_2d"):
        return shapely.force_2d(geom)
    return ops.transform(lambda x, y, *_z: (x, y), geom)


def _reference_z_lookup(reference_geom: BaseGeometry | None) -> tuple[dict[tuple[float, float], float], float]:
    if reference_geom is None or reference_geom.is_empty or not reference_geom.has_z:
        return {}, 0.0
    coords = shapely.get_coordinates(reference_geom, include_z=True)
    if len(coords) == 0:
        return {}, 0.0
    z_vals = coords[:, 2]
    if float(z_vals.min()) == 0.0 and float(z_vals.max()) == 0.0:
        return {}, 0.0
    lookup: dict[tuple[float, float], float] = {}
    for row in coords:
        z_val = float(row[2]) if math.isfinite(float(row[2])) else 0.0
        lookup[(round(float(row[0]), 6), round(float(row[1]), 6))] = z_val
    finite_zs = [float(v) for v in z_vals if math.isfinite(float(v))]
    default_z = (sum(finite_zs) / len(finite_zs)) if finite_zs else 0.0
    return lookup, default_z


def restore_z(
    geom: BaseGeometry | None,
    has_z: bool,
    default_z: float = 0.0,
    reference_geom: BaseGeometry | None = None,
) -> BaseGeometry:
    """Preserve or restore Z dimension so PolygonZ schemas remain compatible."""
    if geom is None or geom.is_empty:
        base = Polygon()
        if has_z and hasattr(shapely, "force_3d"):
            return shapely.force_3d(base, z=default_z)
        return base

    geom_2d = to_2d(geom)
    if not has_z:
        return geom_2d

    lookup, ref_default = _reference_z_lookup(reference_geom)
    if not lookup:
        z_fill = ref_default if reference_geom is not None else default_z
        if hasattr(shapely, "force_3d"):
            return shapely.force_3d(geom_2d, z=z_fill)
        return ops.transform(lambda x, y: (x, y, z_fill), geom_2d)

    def _apply_z(x: Any, y: Any) -> tuple[Any, Any, Any]:
        if isinstance(x, (int, float)):
            return x, y, lookup.get((round(float(x), 6), round(float(y), 6)), ref_default)
        zs = [
            lookup.get((round(float(xi), 6), round(float(yi), 6)), ref_default)
            for xi, yi in zip(x, y, strict=False)
        ]
        return x, y, zs

    return ops.transform(_apply_z, geom_2d)


def _collect_polygons(geom: BaseGeometry, out: list[Polygon], min_area: float) -> None:
    if geom is None or geom.is_empty:
        return
    gtype = geom.geom_type
    if gtype == "Polygon":
        if geom.area > min_area and geom.area > 0.0:
            out.append(geom)
    elif gtype in ("MultiPolygon", "GeometryCollection"):
        for sub in geom.geoms:
            _collect_polygons(sub, out, min_area)


def extract_polygonal(geom: BaseGeometry | None, min_area: float = 0.0) -> BaseGeometry:
    """Extract only valid polygonal components, discarding points and lines."""
    if geom is None or geom.is_empty:
        return Polygon()
    parts: list[Polygon] = []
    _collect_polygons(geom, parts, min_area)
    if not parts:
        return Polygon()
    if len(parts) == 1:
        return parts[0]
    mp = MultiPolygon(parts)
    if mp.is_valid:
        return mp
    merged = ops.unary_union(parts)
    merged_parts: list[Polygon] = []
    _collect_polygons(merged, merged_parts, min_area)
    if not merged_parts:
        return Polygon()
    return merged_parts[0] if len(merged_parts) == 1 else MultiPolygon(merged_parts)


def safe_make_valid(geom: BaseGeometry | None, min_area: float = 0.0) -> BaseGeometry:
    """Repair invalid polygon geometries via structure-preserving make_valid."""
    if geom is None or geom.is_empty:
        return Polygon()
    geom_2d = to_2d(geom)
    if geom_2d.is_valid:
        return extract_polygonal(geom_2d, min_area=min_area)

    repaired: BaseGeometry
    try:
        repaired = shapely.make_valid(geom_2d, method="structure")
    except TypeError:
        repaired = shapely.make_valid(geom_2d)
    except Exception:
        repaired = geom_2d.buffer(0.0)

    poly = extract_polygonal(repaired, min_area=min_area)
    if not poly.is_empty and not poly.is_valid:
        poly = extract_polygonal(poly.buffer(0.0), min_area=min_area)
    return poly


def polsby_popper(area_m2: float, perim_m: float) -> float:
    """Compute Polsby-Popper compactness index: 4 * pi * area / perimeter^2."""
    if area_m2 <= 0.0 or perim_m <= 0.0:
        return 0.0
    score = (4.0 * math.pi * area_m2) / (perim_m * perim_m)
    return max(0.0, min(1.0, float(score)))


def parse_validity_location(explanation: str, fallback_geom: BaseGeometry) -> tuple[float, float]:
    """Extract [x, y] error coordinates from GEOS explain_validity output."""
    match = _VALIDITY_COORD_RE.search(explanation or "")
    if match:
        return float(match.group(1)), float(match.group(2))
    if fallback_geom is not None and not fallback_geom.is_empty:
        rep = fallback_geom.representative_point()
        return float(rep.x), float(rep.y)
    return 0.0, 0.0


def _ring_spike_indices(
    pts: list[tuple[float, float]],
    max_spike_angle_deg: float,
    max_spike_arm_m: float,
) -> list[tuple[int, float, float, float]]:
    n = len(pts)
    if n < 4:
        return []
    hits: list[tuple[int, float, float, float]] = []
    for idx in range(n):
        p_prev = pts[(idx - 1) % n]
        p_curr = pts[idx]
        p_next = pts[(idx + 1) % n]
        vx1 = p_prev[0] - p_curr[0]
        vy1 = p_prev[1] - p_curr[1]
        vx2 = p_next[0] - p_curr[0]
        vy2 = p_next[1] - p_curr[1]
        len1 = math.hypot(vx1, vy1)
        len2 = math.hypot(vx2, vy2)
        if len1 == 0.0 or len2 == 0.0:
            continue
        cos_theta = max(-1.0, min(1.0, (vx1 * vx2 + vy1 * vy2) / (len1 * len2)))
        angle_deg = math.degrees(math.acos(cos_theta))
        min_arm = min(len1, len2)
        if angle_deg < max_spike_angle_deg and min_arm < max_spike_arm_m:
            hits.append((idx, float(p_curr[0]), float(p_curr[1]), float(angle_deg)))
    return hits


def find_boundary_spikes(
    geom: BaseGeometry,
    max_spike_angle_deg: float = 1.0,
    max_spike_arm_m: float = 5.0,
) -> list[tuple[float, float, float]]:
    """Detect acute boundary spike vertices across all exterior and interior rings."""
    if geom is None or geom.is_empty:
        return []
    polys: list[Polygon] = []
    _collect_polygons(geom, polys, min_area=0.0)
    spikes: list[tuple[float, float, float]] = []
    for poly in polys:
        rings = [poly.exterior, *list(poly.interiors)]
        for ring in rings:
            coords = [(float(c[0]), float(c[1])) for c in ring.coords]
            if len(coords) < 4:
                continue
            pts = coords[:-1]
            for _idx, sx, sy, angle_deg in _ring_spike_indices(
                pts, max_spike_angle_deg, max_spike_arm_m
            ):
                spikes.append((sx, sy, angle_deg))
    return spikes


def _prune_ring_coords(
    coords: list[tuple[float, float]],
    max_spike_angle_deg: float,
    max_spike_arm_m: float,
) -> list[tuple[float, float]]:
    if len(coords) < 5:
        return coords
    pts = list(coords[:-1])
    orig_area = float(Polygon([*pts, pts[0]]).area)
    for _ in range(25):
        if len(pts) < 4:
            break
        hits = _ring_spike_indices(pts, max_spike_angle_deg, max_spike_arm_m)
        if not hits:
            break
        removed = False
        for idx, _sx, _sy, _ang in hits:
            candidate = [*pts[:idx], *pts[idx + 1 :]]
            if len(candidate) < 3:
                continue
            test_poly = Polygon([*candidate, candidate[0]])
            if test_poly.is_valid and test_poly.area > 0.0:
                pts = candidate
                removed = True
                break
            repaired = safe_make_valid(test_poly, min_area=1e-4)
            if (
                repaired.geom_type == "Polygon"
                and repaired.area > 0.9 * orig_area
                and len(repaired.interiors) == 0
            ):
                new_pts = [(float(c[0]), float(c[1])) for c in repaired.exterior.coords][:-1]
                if len(new_pts) >= 3 and len(
                    _ring_spike_indices(new_pts, max_spike_angle_deg, max_spike_arm_m)
                ) < len(hits):
                    pts = new_pts
                    removed = True
                    break
        if not removed:
            break
    return [*pts, pts[0]]


def prune_boundary_spikes(
    geom: BaseGeometry,
    max_spike_angle_deg: float = 1.0,
    max_spike_arm_m: float = 5.0,
    min_part_area_m2: float = 0.0,
) -> BaseGeometry:
    """Remove acute boundary spike vertices while maintaining ring validity."""
    if geom is None or geom.is_empty:
        return Polygon()
    polys: list[Polygon] = []
    _collect_polygons(geom, polys, min_area=0.0)
    rebuilt: list[Polygon] = []
    for poly in polys:
        ext_coords = [(float(c[0]), float(c[1])) for c in poly.exterior.coords]
        new_ext = _prune_ring_coords(ext_coords, max_spike_angle_deg, max_spike_arm_m)
        new_holes: list[list[tuple[float, float]]] = []
        for interior in poly.interiors:
            hole_coords = [(float(c[0]), float(c[1])) for c in interior.coords]
            pruned_hole = _prune_ring_coords(hole_coords, max_spike_angle_deg, max_spike_arm_m)
            if len(pruned_hole) >= 4:
                new_holes.append(pruned_hole)
        candidate = Polygon(new_ext, new_holes)
        if not candidate.is_valid:
            candidate_valid = safe_make_valid(candidate, min_area=min_part_area_m2)
            _collect_polygons(candidate_valid, rebuilt, min_area=min_part_area_m2)
        elif candidate.area > min_part_area_m2:
            rebuilt.append(candidate)
    if not rebuilt:
        return Polygon()
    return rebuilt[0] if len(rebuilt) == 1 else extract_polygonal(MultiPolygon(rebuilt), min_area=min_part_area_m2)


def build_marker_polygon(x: float, y: float, radius_m: float = 5.0) -> Polygon:
    """Create a circular polygon marker centered at (x, y) in metric coordinates."""
    return Point(float(x), float(y)).buffer(float(radius_m), quad_segs=16)


def shared_boundary_length(geom_a: BaseGeometry, geom_b: BaseGeometry) -> float:
    """Measure shared boundary length with tolerance fallback for float drift."""
    if geom_a is None or geom_b is None or geom_a.is_empty or geom_b.is_empty:
        return 0.0
    exact_len = float(geom_a.boundary.intersection(geom_b.boundary).length)
    if exact_len > 0.0:
        return exact_len
    return float(geom_a.boundary.intersection(geom_b.buffer(1e-4)).length)


def resolve_oid_ref(attrs: dict[str, Any], fallback_fid: int) -> str:
    """Resolve human-readable object identifier string from feature attributes."""
    for key in _OID_CANDIDATE_KEYS:
        if key not in attrs:
            continue
        val = attrs[key]
        if val is None:
            continue
        if isinstance(val, float) and (math.isnan(val) or not math.isfinite(val)):
            continue
        if isinstance(val, bool):
            continue
        if isinstance(val, int):
            return str(val)
        text = str(val).strip()
        if text and text.lower() not in ("nan", "none", "null"):
            return text
    return str(fallback_fid)


def _polygon_part_count(geom: BaseGeometry) -> int:
    if geom is None or geom.is_empty:
        return 0
    if geom.geom_type == "Polygon":
        return 1
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        return len(geom.geoms)
    return 0


def merge_adjacent_polygons(neighbor: BaseGeometry, piece: BaseGeometry) -> BaseGeometry:
    """Union an adjacent gap or sliver piece into a neighbor, healing hairline cracks."""
    merged = extract_polygonal(safe_make_valid(ops.unary_union([neighbor, piece])))
    orig_parts = _polygon_part_count(neighbor)
    if _polygon_part_count(merged) <= orig_parts or not hasattr(shapely, "snap"):
        return merged

    dist = float(neighbor.distance(piece))
    snap_tol = max(1e-6, min(1e-3, dist * 2.0 + 1e-5))
    snapped_piece = shapely.snap(piece, neighbor, snap_tol)
    merged_piece = extract_polygonal(
        safe_make_valid(ops.unary_union([neighbor, snapped_piece]))
    )
    if not merged_piece.is_empty and _polygon_part_count(merged_piece) <= orig_parts:
        return merged_piece

    snapped_nbr = shapely.snap(neighbor, piece, snap_tol)
    merged_nbr = extract_polygonal(
        safe_make_valid(ops.unary_union([snapped_nbr, piece]))
    )
    if not merged_nbr.is_empty and _polygon_part_count(merged_nbr) <= orig_parts:
        return merged_nbr

    return merged
