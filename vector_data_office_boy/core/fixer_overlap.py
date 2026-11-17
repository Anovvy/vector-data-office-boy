"""Automated resolution for contained, partial, and micro polygon overlaps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import shapely
from shapely import STRtree, ops
from shapely.geometry import GeometryCollection, Polygon
from shapely.geometry.base import BaseGeometry

from vector_data_office_boy.core.detector import scan_overlaps
from vector_data_office_boy.core.geom_backend import (
    build_metric_context,
    extract_polygonal,
    find_boundary_spikes,
    prune_boundary_spikes,
    safe_make_valid,
    shared_boundary_length,
)
from vector_data_office_boy.core.models import (
    ERROR_OVERLAP,
    FixResult,
    OverlapConfig,
)
from vector_data_office_boy.core.overwrite_writer import (
    commit_metric_geometries,
    load_vector_source,
)


def _count_remaining_overlaps(
    geoms: list[BaseGeometry],
    min_area: float = 0.0,
    strict_positive_only: bool = True,
) -> int:
    tree = STRtree(geoms)
    count = 0
    for i, gi in enumerate(geoms):
        if gi is None or gi.is_empty:
            continue
        for j_raw in tree.query(gi, predicate="intersects"):
            j = int(j_raw)
            if j <= i:
                continue
            gj = geoms[j]
            if gj is None or gj.is_empty:
                continue
            inter = extract_polygonal(gi.intersection(gj))
            if inter.is_empty:
                continue
            area = float(inter.area)
            if strict_positive_only:
                if area > 0.0:
                    count += 1
            elif area >= min_area:
                count += 1
    return count


def _choose_loser_pair(
    idx_a: int,
    idx_b: int,
    geom_a: BaseGeometry,
    geom_b: BaseGeometry,
    fids: list[int],
    overlap_rule: str,
    contained_mode: bool = False,
) -> tuple[int, int]:
    if geom_a.covers(geom_b) and geom_b.covers(geom_a):
        return (idx_a, idx_b) if fids[idx_a] <= fids[idx_b] else (idx_b, idx_a)

    if contained_mode:
        if overlap_rule in ("cut_hole_in_host", "subtract_from_larger"):
            if geom_a.covers(geom_b):
                return idx_b, idx_a
            if geom_b.covers(geom_a):
                return idx_a, idx_b
        elif overlap_rule == "keep_lower_fid":
            return (idx_a, idx_b) if fids[idx_a] <= fids[idx_b] else (idx_b, idx_a)

    if overlap_rule == "keep_lower_fid":
        return (idx_a, idx_b) if fids[idx_a] <= fids[idx_b] else (idx_b, idx_a)

    if geom_a.area >= geom_b.area:
        return idx_b, idx_a
    return idx_a, idx_b


def _subtract_loser(
    geoms: list[BaseGeometry],
    winner_idx: int,
    loser_idx: int,
) -> None:
    winner = geoms[winner_idx]
    loser = geoms[loser_idx]
    if winner.is_empty or loser.is_empty:
        return
    updated = extract_polygonal(loser.difference(winner))
    if not updated.is_empty and not updated.is_valid:
        updated = safe_make_valid(updated)
    geoms[loser_idx] = updated


def _precision_coverage_noding(
    geoms: list[BaseGeometry],
    fids: list[int],
    overlap_rule: str,
) -> list[BaseGeometry]:
    last_error: Exception | None = None
    for grid_size in (1e-5, 1e-4, 1e-3):
        try:
            snapped = [
                extract_polygonal(shapely.set_precision(g, grid_size))
                if not g.is_empty
                else Polygon()
                for g in geoms
            ]
            boundaries = [g.boundary for g in snapped if not g.is_empty]
            if not boundaries:
                return geoms
            noded = shapely.node(GeometryCollection(boundaries))
            faces = list(shapely.polygonize([noded]).geoms)

            stree = STRtree(snapped)
            otree = STRtree(geoms)
            owned: list[list[Polygon]] = [[] for _ in geoms]

            for face in faces:
                if face.is_empty or face.area <= 0.0:
                    continue
                pt = face.representative_point()
                owners = [
                    int(idx)
                    for idx in stree.query(pt, predicate="intersects")
                    if not snapped[int(idx)].is_empty and snapped[int(idx)].covers(pt)
                ]
                if not owners:
                    owners = [
                        int(idx)
                        for idx in otree.query(pt, predicate="intersects")
                        if not geoms[int(idx)].is_empty and geoms[int(idx)].covers(pt)
                    ]
                if not owners and face.area < 1e-2:
                    cands: list[tuple[float, int]] = []
                    for idx_raw in otree.query(face, predicate="intersects"):
                        idx = int(idx_raw)
                        if geoms[idx].is_empty:
                            continue
                        ia = float(geoms[idx].intersection(face).area)
                        if ia > 0.1 * float(face.area):
                            cands.append((ia, idx))
                    if cands:
                        if overlap_rule == "keep_lower_fid":
                            cands.sort(key=lambda item: fids[item[1]])
                        else:
                            cands.sort(key=lambda item: (-item[0], geoms[item[1]].area, fids[item[1]]))
                        owners = [cands[0][1]]

                if not owners:
                    continue
                if len(owners) > 1:
                    if overlap_rule == "keep_lower_fid":
                        owners.sort(key=lambda idx: fids[idx])
                    else:
                        owners.sort(key=lambda idx: (geoms[idx].area, fids[idx]))
                owned[owners[0]].append(face)

            reconstructed: list[BaseGeometry] = []
            for flist in owned:
                if not flist:
                    reconstructed.append(Polygon())
                elif len(flist) == 1:
                    reconstructed.append(flist[0])
                else:
                    merged = extract_polygonal(ops.unary_union(flist))
                    if not merged.is_empty and not merged.is_valid:
                        merged = safe_make_valid(merged)
                    reconstructed.append(merged)
            return _cleanup_noding_micro_spikes(reconstructed, geoms)
        except Exception as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise RuntimeError(
            f"Precision coverage noding failed across grid tolerances: {last_error}"
        ) from last_error
    return geoms


def _extract_union_interior_polys(geoms: list[BaseGeometry]) -> list[Polygon]:
    non_empty = [g for g in geoms if g is not None and not g.is_empty]
    if not non_empty:
        return []
    union_geom = ops.unary_union(non_empty)
    polys: list[Polygon] = []
    if union_geom.geom_type == "Polygon":
        polys = [union_geom]
    elif union_geom.geom_type == "MultiPolygon":
        polys = list(union_geom.geoms)
    elif union_geom.geom_type == "GeometryCollection":
        for sub in union_geom.geoms:
            if sub.geom_type == "Polygon":
                polys.append(sub)
            elif sub.geom_type == "MultiPolygon":
                polys.extend(sub.geoms)
    return [
        Polygon(ring)
        for poly in polys
        for ring in poly.interiors
        if Polygon(ring).area > 0.0
    ]


def _cleanup_noding_micro_spikes(
    reconstructed: list[BaseGeometry],
    pre_noding_geoms: list[BaseGeometry],
) -> list[BaseGeometry]:
    had_pre_spikes = [
        bool(g is not None and not g.is_empty and find_boundary_spikes(g, 1.0, 5.0))
        for g in pre_noding_geoms
    ]
    new_spike_exists = any(
        not had_pre_spikes[idx]
        and not g.is_empty
        and bool(find_boundary_spikes(g, 1.0, 5.0))
        for idx, g in enumerate(reconstructed)
    )
    if not new_spike_exists:
        return reconstructed

    orig_gap_polys = _extract_union_interior_polys(reconstructed)
    cleaned = list(reconstructed)

    for _ in range(3):
        changed = False
        for idx, geom in enumerate(cleaned):
            if had_pre_spikes[idx] or geom.is_empty:
                continue
            if find_boundary_spikes(geom, 1.0, 5.0):
                cleaned[idx] = prune_boundary_spikes(geom, 1.0, 5.0)
                changed = True

        if not changed:
            break

        post_gaps = _extract_union_interior_polys(cleaned)
        tree = STRtree(cleaned)
        for gap_poly in post_gaps:
            if gap_poly.area > 1e-2:
                continue
            if any(
                gap_poly.intersection(og).area > 0.5 * gap_poly.area
                for og in orig_gap_polys
            ):
                continue
            best_j: int | None = None
            best_sb = -1.0
            best_area = -1.0
            for j_raw in tree.query(gap_poly.buffer(1e-3), predicate="intersects"):
                j = int(j_raw)
                if cleaned[j].is_empty:
                    continue
                sb = shared_boundary_length(gap_poly, cleaned[j])
                if sb > best_sb or (sb == best_sb and cleaned[j].area > best_area):
                    best_sb = sb
                    best_area = float(cleaned[j].area)
                    best_j = j
            if best_j is not None:
                merged = extract_polygonal(
                    safe_make_valid(ops.unary_union([cleaned[best_j], gap_poly]))
                )
                cleaned[best_j] = merged
                tree = STRtree(cleaned)

    return cleaned


def fix_overlaps(
    source: str | Path | Any,
    config: OverlapConfig | None = None,
) -> FixResult:
    """Resolve contained, partial, and micro overlaps, then overwrite the source."""
    cfg = config or OverlapConfig()
    loaded = load_vector_source(source)
    ctx = build_metric_context(loaded.crs_wkt, loaded.geometries_2d)
    orig_metric = [ctx.to_metric(g) for g in loaded.geometries_2d]
    features_before = len(orig_metric)
    logs: list[str] = []

    if ctx.was_reprojected:
        logs.append(
            f"Normalized CRS to metric UTM for overlap resolution ({loaded.layer_name})."
        )

    working: list[BaseGeometry] = []
    for geom_m in orig_metric:
        if geom_m is None or geom_m.is_empty:
            working.append(Polygon())
        elif geom_m.is_valid:
            working.append(extract_polygonal(geom_m))
        else:
            working.append(safe_make_valid(geom_m))

    initial_matching = _count_remaining_overlaps(
        working,
        min_area=cfg.min_overlap_area_m2,
        strict_positive_only=cfg.include_micro_overlaps,
    )
    initial_positive = _count_remaining_overlaps(working, strict_positive_only=True)

    # Pass 1: resolve 100% contained overlaps (Option A cuts hole in host by default)
    contained_resolved = 0
    tree = STRtree(working)
    pair_list = [
        (i, int(j))
        for i, gi in enumerate(working)
        if not gi.is_empty
        for j in tree.query(gi, predicate="intersects")
        if int(j) > i and not working[int(j)].is_empty
    ]

    for i, j in pair_list:
        gi, gj = working[i], working[j]
        if gi.is_empty or gj.is_empty:
            continue
        if not (gi.covers(gj) or gj.covers(gi)):
            continue
        inter = extract_polygonal(gi.intersection(gj))
        if inter.is_empty or float(inter.area) <= 0.0:
            continue
        winner_idx, loser_idx = _choose_loser_pair(
            i, j, gi, gj, loaded.fids, cfg.overlap_rule, contained_mode=True
        )
        _subtract_loser(working, winner_idx, loser_idx)
        contained_resolved += 1

    # Pass 2: resolve partial and triple overlaps above threshold
    partial_resolved = 0
    min_thresh = 0.0 if cfg.include_micro_overlaps else cfg.min_overlap_area_m2
    tree = STRtree(working)
    pair_list = [
        (i, int(j))
        for i, gi in enumerate(working)
        if not gi.is_empty
        for j in tree.query(gi, predicate="intersects")
        if int(j) > i and not working[int(j)].is_empty
    ]
    for i, j in pair_list:
        gi, gj = working[i], working[j]
        if gi.is_empty or gj.is_empty:
            continue
        inter = extract_polygonal(gi.intersection(gj))
        if inter.is_empty:
            continue
        area = float(inter.area)
        if (cfg.include_micro_overlaps and area <= 0.0) or (
            not cfg.include_micro_overlaps and area < min_thresh
        ):
            continue
        winner_idx, loser_idx = _choose_loser_pair(
            i, j, gi, gj, loaded.fids, cfg.overlap_rule, contained_mode=False
        )
        _subtract_loser(working, winner_idx, loser_idx)
        partial_resolved += 1

    # Pass 3: precision coverage noding pass so zero overlaps > 0 remain
    remaining_pos = _count_remaining_overlaps(working, strict_positive_only=True)
    noding_applied = False
    if remaining_pos > 0:
        working = _precision_coverage_noding(working, loaded.fids, cfg.overlap_rule)
        noding_applied = True
        remaining_pos = _count_remaining_overlaps(working, strict_positive_only=True)
        if remaining_pos > 0:
            tree = STRtree(working)
            for i, gi in enumerate(working):
                if gi.is_empty:
                    continue
                for j_raw in tree.query(gi, predicate="intersects"):
                    j = int(j_raw)
                    if j <= i or working[j].is_empty:
                        continue
                    inter = extract_polygonal(gi.intersection(working[j]))
                    if not inter.is_empty and float(inter.area) > 0.0:
                        winner_idx, loser_idx = _choose_loser_pair(
                            i,
                            j,
                            working[i],
                            working[j],
                            loaded.fids,
                            cfg.overlap_rule,
                            contained_mode=False,
                        )
                        _subtract_loser(working, winner_idx, loser_idx)
                        if loser_idx == i:
                            gi = working[i]

    out_path, features_after, area_changed_m2 = commit_metric_geometries(
        loaded=loaded,
        ctx=ctx,
        orig_metric_geoms=orig_metric,
        fixed_metric_geoms=working,
        recalc_attributes=cfg.recalc_attributes,
    )

    rescan = scan_overlaps(source, cfg)
    fixed_count = (
        max(0, initial_matching - rescan.error_count)
        if initial_matching > 0
        else (initial_positive if noding_applied else 0)
    )

    logs.append(
        f"Overlap repair ({cfg.overlap_rule}): contained={contained_resolved}, "
        f"partial={partial_resolved}, noding_pass={noding_applied}, "
        f"remaining={rescan.error_count}."
    )
    return FixResult(
        error_type=ERROR_OVERLAP,
        fixed_count=fixed_count,
        features_before=features_before,
        features_after=features_after,
        remaining_errors=rescan.error_count,
        area_changed_m2=area_changed_m2,
        output_path=out_path,
        logs=logs,
    )
