"""Topology error scanners for self-intersections, overlaps, gaps, and slivers."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from shapely import STRtree, ops
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from vector_data_office_boy.core.geom_backend import (
    MetricCrsContext,
    build_marker_polygon,
    build_metric_context,
    extract_polygonal,
    find_boundary_spikes,
    parse_validity_location,
    polsby_popper,
    resolve_oid_ref,
    safe_make_valid,
    shared_boundary_length,
)
from vector_data_office_boy.core.models import (
    ERROR_GAP,
    ERROR_LAYER_NAMES,
    ERROR_OVERLAP,
    ERROR_SELF_INTERSECTION,
    ERROR_SLIVER,
    GapConfig,
    OverlapConfig,
    ScanResult,
    SelfIntersectionConfig,
    SliverConfig,
    TopologyErrorRecord,
)
from vector_data_office_boy.core.overwrite_writer import LoadedLayerData, load_vector_source


def _prepare_metric_geoms(
    source: str | Path | Any,
) -> tuple[LoadedLayerData, MetricCrsContext, list[BaseGeometry], list[str]]:
    loaded = load_vector_source(source)
    ctx = build_metric_context(loaded.crs_wkt, loaded.geometries_2d)
    metric_geoms = [ctx.to_metric(g) for g in loaded.geometries_2d]
    oid_refs = [
        resolve_oid_ref(attrs, fid)
        for attrs, fid in zip(loaded.attributes, loaded.fids, strict=False)
    ]
    return loaded, ctx, metric_geoms, oid_refs


def _make_record(
    error_id: int,
    error_type: str,
    severity: str,
    fid_1: int,
    fid_2: str,
    oid_ref: str,
    geom_metric: BaseGeometry,
    ctx: MetricCrsContext,
    error_xy_metric: tuple[float, float] | None = None,
) -> TopologyErrorRecord:
    area_m2 = float(geom_metric.area)
    perim_m = float(geom_metric.length)
    pp = polsby_popper(area_m2, perim_m)
    if error_xy_metric is None:
        rep = geom_metric.representative_point()
        mx, my = float(rep.x), float(rep.y)
    else:
        mx, my = error_xy_metric
    err_x, err_y = ctx.transform_point_to_source(mx, my)
    geom_src = ctx.to_source(geom_metric)
    return TopologyErrorRecord(
        error_id=error_id,
        error_type=error_type,
        severity=severity,
        fid_1=int(fid_1),
        fid_2=str(fid_2),
        oid_ref=str(oid_ref),
        area_m2=area_m2,
        perim_m=perim_m,
        pp_index=pp,
        error_x=float(err_x),
        error_y=float(err_y),
        geometry_wkb=geom_src.wkb,
    )


def scan_ring_self_intersections(
    source: str | Path | Any,
    config: SelfIntersectionConfig | None = None,
) -> ScanResult:
    """Detect invalid self-intersecting rings and optional extreme boundary spikes."""
    cfg = config or SelfIntersectionConfig()
    loaded, ctx, metric_geoms, oid_refs = _prepare_metric_geoms(source)
    logs: list[str] = []
    if ctx.was_reprojected:
        logs.append(
            f"Normalized geographic/non-metric CRS to metric UTM for analysis ({loaded.layer_name})."
        )

    records: list[TopologyErrorRecord] = []
    invalid_count = 0
    spike_count = 0

    for fid, oid_ref, geom_m in zip(loaded.fids, oid_refs, metric_geoms, strict=False):
        if geom_m is None or geom_m.is_empty:
            continue
        emitted_pts: list[tuple[float, float]] = []

        if not geom_m.is_valid:
            explanation = explain_validity(geom_m)
            mx, my = parse_validity_location(explanation, geom_m)
            marker_m = build_marker_polygon(mx, my, radius_m=5.0)
            records.append(
                _make_record(
                    error_id=len(records) + 1,
                    error_type=ERROR_SELF_INTERSECTION,
                    severity="CRITICAL",
                    fid_1=fid,
                    fid_2="",
                    oid_ref=oid_ref,
                    geom_metric=marker_m,
                    ctx=ctx,
                    error_xy_metric=(mx, my),
                )
            )
            emitted_pts.append((mx, my))
            invalid_count += 1

        if cfg.detect_spikes:
            spikes = find_boundary_spikes(
                geom_m,
                max_spike_angle_deg=cfg.max_spike_angle_deg,
                max_spike_arm_m=cfg.max_spike_arm_m,
            )
            for sx, sy, _angle_deg in spikes:
                if any(math.hypot(sx - px, sy - py) < 0.1 for px, py in emitted_pts):
                    continue
                marker_m = build_marker_polygon(sx, sy, radius_m=5.0)
                records.append(
                    _make_record(
                        error_id=len(records) + 1,
                        error_type=ERROR_SELF_INTERSECTION,
                        severity="MEDIUM",
                        fid_1=fid,
                        fid_2="",
                        oid_ref=oid_ref,
                        geom_metric=marker_m,
                        ctx=ctx,
                        error_xy_metric=(sx, sy),
                    )
                )
                emitted_pts.append((sx, sy))
                spike_count += 1

    logs.append(
        f"Self-intersection scan completed on {len(metric_geoms)} features: "
        f"{invalid_count} invalid ring(s), {spike_count} spike(s)."
    )
    return ScanResult(
        error_type=ERROR_SELF_INTERSECTION,
        layer_name=ERROR_LAYER_NAMES[ERROR_SELF_INTERSECTION],
        crs_wkt=loaded.crs_wkt,
        error_count=len(records),
        total_area_m2=float(sum(r.area_m2 for r in records)),
        records=records,
        logs=logs,
    )


def scan_overlaps(
    source: str | Path | Any,
    config: OverlapConfig | None = None,
) -> ScanResult:
    """Detect pairwise and 100% contained polygon overlaps using an STRtree index."""
    cfg = config or OverlapConfig()
    loaded, ctx, metric_geoms, oid_refs = _prepare_metric_geoms(source)
    logs: list[str] = []
    if ctx.was_reprojected:
        logs.append(
            f"Normalized CRS to metric UTM for overlap scan ({loaded.layer_name})."
        )

    working: list[BaseGeometry] = []
    for geom_m in metric_geoms:
        if geom_m is None or geom_m.is_empty:
            working.append(Polygon())
        elif geom_m.is_valid:
            working.append(extract_polygonal(geom_m))
        else:
            working.append(safe_make_valid(geom_m))

    tree = STRtree(working)
    records: list[TopologyErrorRecord] = []
    contained_count = 0

    for i, gi in enumerate(working):
        if gi.is_empty:
            continue
        candidates = sorted(int(j) for j in tree.query(gi, predicate="intersects") if int(j) > i)
        for j in candidates:
            gj = working[j]
            if gj.is_empty:
                continue
            inter = extract_polygonal(gi.intersection(gj))
            if inter.is_empty:
                continue
            area_m2 = float(inter.area)
            if cfg.include_micro_overlaps:
                if area_m2 <= 0.0:
                    continue
            elif area_m2 < cfg.min_overlap_area_m2:
                continue

            is_contained = bool(gi.covers(gj) or gj.covers(gi))
            if is_contained:
                contained_count += 1
            severity = "HIGH" if (is_contained or area_m2 >= 1.0) else "MEDIUM"
            records.append(
                _make_record(
                    error_id=len(records) + 1,
                    error_type=ERROR_OVERLAP,
                    severity=severity,
                    fid_1=loaded.fids[i],
                    fid_2=str(loaded.fids[j]),
                    oid_ref=f"{oid_refs[i]},{oid_refs[j]}",
                    geom_metric=inter,
                    ctx=ctx,
                )
            )

    logs.append(
        f"Overlap scan completed on {len(working)} features: "
        f"{len(records)} overlap(s) ({contained_count} fully contained)."
    )
    return ScanResult(
        error_type=ERROR_OVERLAP,
        layer_name=ERROR_LAYER_NAMES[ERROR_OVERLAP],
        crs_wkt=loaded.crs_wkt,
        error_count=len(records),
        total_area_m2=float(sum(r.area_m2 for r in records)),
        records=records,
        logs=logs,
    )


def scan_internal_gaps(
    source: str | Path | Any,
    config: GapConfig | None = None,
) -> ScanResult:
    """Detect interior voids strictly inside unary_union without touching outer bounds."""
    _ = config or GapConfig()
    loaded, ctx, metric_geoms, oid_refs = _prepare_metric_geoms(source)
    logs: list[str] = []
    if ctx.was_reprojected:
        logs.append(
            f"Normalized CRS to metric UTM for internal gap scan ({loaded.layer_name})."
        )

    working: list[BaseGeometry] = []
    for geom_m in metric_geoms:
        if geom_m is None or geom_m.is_empty:
            working.append(Polygon())
        elif geom_m.is_valid:
            working.append(extract_polygonal(geom_m))
        else:
            working.append(safe_make_valid(geom_m))

    non_empty = [g for g in working if not g.is_empty]
    records: list[TopologyErrorRecord] = []

    if non_empty:
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

        tree = STRtree(working)
        for poly in union_polys:
            for ring in poly.interiors:
                gap_m = Polygon(ring)
                if gap_m.is_empty or gap_m.area <= 0.0:
                    continue
                if not gap_m.is_valid:
                    gap_m = safe_make_valid(gap_m)
                    if gap_m.is_empty:
                        continue

                search_geom = gap_m.buffer(1e-3)
                cand_indices = [
                    int(idx)
                    for idx in tree.query(search_geom, predicate="intersects")
                    if not working[int(idx)].is_empty
                ]
                scored_nbrs: list[tuple[float, int]] = []
                for idx in cand_indices:
                    sb = shared_boundary_length(gap_m, working[idx])
                    scored_nbrs.append((sb, idx))
                scored_nbrs.sort(key=lambda item: (-item[0], loaded.fids[item[1]]))
                nbr_indices = [idx for _sb, idx in scored_nbrs]

                primary_fid = loaded.fids[nbr_indices[0]] if nbr_indices else -1
                fid_2_str = ",".join(str(loaded.fids[idx]) for idx in nbr_indices)
                oid_str = ",".join(oid_refs[idx] for idx in nbr_indices)
                severity = "HIGH" if gap_m.area >= 10.0 else "MEDIUM"

                records.append(
                    _make_record(
                        error_id=len(records) + 1,
                        error_type=ERROR_GAP,
                        severity=severity,
                        fid_1=primary_fid,
                        fid_2=fid_2_str,
                        oid_ref=oid_str,
                        geom_metric=gap_m,
                        ctx=ctx,
                    )
                )

    logs.append(
        f"Internal gap scan completed on {len(working)} features: "
        f"{len(records)} internal gap(s) detected."
    )
    return ScanResult(
        error_type=ERROR_GAP,
        layer_name=ERROR_LAYER_NAMES[ERROR_GAP],
        crs_wkt=loaded.crs_wkt,
        error_count=len(records),
        total_area_m2=float(sum(r.area_m2 for r in records)),
        records=records,
        logs=logs,
    )


def scan_slivers(
    source: str | Path | Any,
    config: SliverConfig | None = None,
) -> ScanResult:
    """Detect standalone micro/thin slivers and disconnected MultiPolygon fragments."""
    cfg = config or SliverConfig()
    loaded, ctx, metric_geoms, oid_refs = _prepare_metric_geoms(source)
    logs: list[str] = []
    if ctx.was_reprojected:
        logs.append(
            f"Normalized CRS to metric UTM for sliver scan ({loaded.layer_name})."
        )

    records: list[TopologyErrorRecord] = []
    standalone_count = 0
    multipart_count = 0

    for fid, oid_ref, geom_m in zip(loaded.fids, oid_refs, metric_geoms, strict=False):
        if geom_m is None or geom_m.is_empty:
            continue
        poly_geom = extract_polygonal(geom_m) if geom_m.geom_type == "GeometryCollection" else geom_m
        if poly_geom.is_empty:
            continue

        area_m2 = float(poly_geom.area)
        perim_m = float(poly_geom.length)
        pp = polsby_popper(area_m2, perim_m)
        is_micro = area_m2 <= cfg.max_micro_sliver_area_m2
        is_thin = pp <= cfg.min_polsby_popper and area_m2 <= cfg.max_thin_sliver_area_m2

        if is_micro or is_thin:
            records.append(
                _make_record(
                    error_id=len(records) + 1,
                    error_type=ERROR_SLIVER,
                    severity="HIGH" if is_micro else "MEDIUM",
                    fid_1=fid,
                    fid_2="",
                    oid_ref=oid_ref,
                    geom_metric=poly_geom,
                    ctx=ctx,
                )
            )
            standalone_count += 1
        elif poly_geom.geom_type == "MultiPolygon":
            for part_idx, part in enumerate(poly_geom.geoms):
                if part.area <= cfg.min_multipart_area_m2:
                    records.append(
                        _make_record(
                            error_id=len(records) + 1,
                            error_type=ERROR_SLIVER,
                            severity="HIGH",
                            fid_1=fid,
                            fid_2=f"part_{part_idx}",
                            oid_ref=oid_ref,
                            geom_metric=part,
                            ctx=ctx,
                        )
                    )
                    multipart_count += 1

    logs.append(
        f"Sliver scan completed on {len(metric_geoms)} features: "
        f"{standalone_count} standalone sliver(s), {multipart_count} multipart fragment(s)."
    )
    return ScanResult(
        error_type=ERROR_SLIVER,
        layer_name=ERROR_LAYER_NAMES[ERROR_SLIVER],
        crs_wkt=loaded.crs_wkt,
        error_count=len(records),
        total_area_m2=float(sum(r.area_m2 for r in records)),
        records=records,
        logs=logs,
    )
