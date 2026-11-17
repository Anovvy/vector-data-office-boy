"""Automated internal gap closure strictly preserving outer dataset boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shapely import STRtree, ops
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from vector_data_office_boy.core.detector import scan_internal_gaps
from vector_data_office_boy.core.geom_backend import (
    build_metric_context,
    extract_polygonal,
    merge_adjacent_polygons,
    safe_make_valid,
    shared_boundary_length,
)
from vector_data_office_boy.core.models import (
    ERROR_GAP,
    FixResult,
    GapConfig,
)
from vector_data_office_boy.core.overwrite_writer import (
    commit_metric_geometries,
    load_vector_source,
)


def _extract_union_gaps(geoms: list[BaseGeometry]) -> list[Polygon]:
    non_empty = [g for g in geoms if g is not None and not g.is_empty]
    if not non_empty:
        return []
    union_geom = ops.unary_union(non_empty)
    union_polys: list[Polygon] = []
    if union_geom.geom_type == "Polygon":
        union_polys = [union_geom]
    elif union_geom.geom_type == "MultiPolygon":
        union_polys = list(union_geom.geoms)
    elif union_geom.geom_type == "GeometryCollection":
        for sub in union_geom.geoms:
            if sub.geom_type == "Polygon":
                union_polys.append(sub)
            elif sub.geom_type == "MultiPolygon":
                union_polys.extend(sub.geoms)

    gaps: list[Polygon] = []
    for poly in union_polys:
        for ring in poly.interiors:
            gap = Polygon(ring)
            if gap.is_empty or gap.area <= 0.0:
                continue
            if not gap.is_valid:
                valid_gap = safe_make_valid(gap)
                if valid_gap.geom_type == "Polygon" and not valid_gap.is_empty:
                    gaps.append(valid_gap)
                elif valid_gap.geom_type == "MultiPolygon":
                    gaps.extend(valid_gap.geoms)
            else:
                gaps.append(gap)
    return gaps


def _select_gap_neighbor(
    gap: Polygon,
    working: list[BaseGeometry],
    fids: list[int],
    tree: STRtree,
    merge_rule: str,
) -> int | None:
    cands: list[tuple[int, float, float]] = []
    for tol in (1e-3, 1e-2):
        for idx_raw in tree.query(gap.buffer(tol), predicate="intersects"):
            idx = int(idx_raw)
            ng = working[idx]
            if ng.is_empty:
                continue
            sb = shared_boundary_length(gap, ng)
            if sb > 0.0 or tol > 1e-3:
                cands.append((idx, sb, float(ng.area)))
        if cands:
            break

    if not cands:
        return None

    if merge_rule == "longest_shared_boundary":
        cands.sort(key=lambda item: (-item[1], -item[2], fids[item[0]]))
    elif merge_rule == "largest_neighbor_area":
        cands.sort(key=lambda item: (-item[2], -item[1], fids[item[0]]))
    else:  # smallest_neighbor_area
        cands.sort(key=lambda item: (item[2], -item[1], fids[item[0]]))
    return cands[0][0]


def fix_internal_gaps(
    source: str | Path | Any,
    config: GapConfig | None = None,
) -> FixResult:
    """Close all internal voids without modifying the outer dataset boundary."""
    cfg = config or GapConfig()
    loaded = load_vector_source(source)
    ctx = build_metric_context(loaded.crs_wkt, loaded.geometries_2d)
    orig_metric = [ctx.to_metric(g) for g in loaded.geometries_2d]
    features_before = len(orig_metric)
    logs: list[str] = []

    if ctx.was_reprojected:
        logs.append(
            f"Normalized CRS to metric UTM for internal gap closure ({loaded.layer_name})."
        )

    working: list[BaseGeometry] = []
    for geom_m in orig_metric:
        if geom_m is None or geom_m.is_empty:
            working.append(Polygon())
        elif geom_m.is_valid:
            working.append(extract_polygonal(geom_m))
        else:
            working.append(safe_make_valid(geom_m))

    gaps = _extract_union_gaps(working)
    modified_flags = [False] * len(working)
    closed_count = 0

    if gaps:
        tree = STRtree(working)
        for gap in gaps:
            target_idx = _select_gap_neighbor(
                gap=gap,
                working=working,
                fids=loaded.fids,
                tree=tree,
                merge_rule=cfg.merge_rule,
            )
            if target_idx is None:
                continue
            working[target_idx] = merge_adjacent_polygons(working[target_idx], gap)
            modified_flags[target_idx] = True
            closed_count += 1
            tree = STRtree(working)

    out_path, features_after, area_changed_m2 = commit_metric_geometries(
        loaded=loaded,
        ctx=ctx,
        orig_metric_geoms=orig_metric,
        fixed_metric_geoms=working,
        recalc_attributes=cfg.recalc_attributes,
        modified_flags=modified_flags,
    )

    rescan = scan_internal_gaps(source, cfg)
    logs.append(
        f"Internal gap closure ({cfg.merge_rule}): {closed_count} gap(s) merged, "
        f"{rescan.error_count} gap(s) remaining."
    )
    return FixResult(
        error_type=ERROR_GAP,
        fixed_count=closed_count,
        features_before=features_before,
        features_after=features_after,
        remaining_errors=rescan.error_count,
        area_changed_m2=area_changed_m2,
        output_path=out_path,
        logs=logs,
    )
