"""Automated cleaning of multipart fragments and standalone sliver polygons."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shapely import STRtree
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from vector_data_office_boy.core.detector import scan_slivers
from vector_data_office_boy.core.geom_backend import (
    build_metric_context,
    extract_polygonal,
    merge_adjacent_polygons,
    polsby_popper,
    safe_make_valid,
    shared_boundary_length,
)
from vector_data_office_boy.core.models import (
    ERROR_SLIVER,
    FixResult,
    SliverConfig,
)
from vector_data_office_boy.core.overwrite_writer import (
    commit_metric_geometries,
    load_vector_source,
)


def _is_standalone_sliver(geom: BaseGeometry, cfg: SliverConfig) -> bool:
    if geom is None or geom.is_empty:
        return False
    area = float(geom.area)
    perim = float(geom.length)
    pp = polsby_popper(area, perim)
    if area <= cfg.max_micro_sliver_area_m2:
        return True
    return pp <= cfg.min_polsby_popper and area <= cfg.max_thin_sliver_area_m2


def _norm_attr(val: Any) -> str:
    if val is None:
        return ""
    text = str(val).strip()
    if text.lower() in ("nan", "none", "null"):
        return ""
    return text


def _clean_multipart_fragments(
    working: list[BaseGeometry],
    fids: list[int],
    modified_flags: list[bool],
    cfg: SliverConfig,
) -> int:
    cleaned_count = 0
    tree = STRtree(working)

    for idx in range(len(working)):
        geom = working[idx]
        if geom.is_empty or geom.geom_type != "MultiPolygon":
            continue
        if _is_standalone_sliver(geom, cfg):
            continue

        keep_parts: list[Polygon] = []
        tiny_parts: list[Polygon] = []
        for part in geom.geoms:
            if part.area <= cfg.min_multipart_area_m2:
                tiny_parts.append(part)
            else:
                keep_parts.append(part)

        if not tiny_parts or not keep_parts:
            continue

        for part in tiny_parts:
            cand_indices = [
                int(j)
                for j in tree.query(part.buffer(1e-3), predicate="intersects")
                if int(j) != idx and not working[int(j)].is_empty
            ]
            overlap_area = 0.0
            is_covered = False
            best_nbr_idx: int | None = None
            best_sb = -1.0
            best_nbr_area = -1.0

            for j in cand_indices:
                other = working[j]
                if other.covers(part):
                    is_covered = True
                inter = extract_polygonal(part.intersection(other))
                if not inter.is_empty:
                    overlap_area += float(inter.area)
                sb = shared_boundary_length(part, other)
                if sb > 0.0 and (
                    sb > best_sb or (sb == best_sb and other.area > best_nbr_area)
                ):
                    best_sb = sb
                    best_nbr_area = float(other.area)
                    best_nbr_idx = j

            covered_ratio = (overlap_area / float(part.area)) if part.area > 0.0 else 1.0
            if not is_covered and covered_ratio < 0.8 and best_nbr_idx is not None:
                working[best_nbr_idx] = merge_adjacent_polygons(
                    working[best_nbr_idx], part
                )
                modified_flags[best_nbr_idx] = True

            cleaned_count += 1

        rebuilt = keep_parts[0] if len(keep_parts) == 1 else MultiPolygon(keep_parts)
        working[idx] = extract_polygonal(safe_make_valid(rebuilt))
        modified_flags[idx] = True
        tree = STRtree(working)

    _ = fids
    return cleaned_count


def _select_sliver_neighbor(
    sliver_idx: int,
    sliver_geom: BaseGeometry,
    working: list[BaseGeometry],
    attributes: list[dict[str, Any]],
    fids: list[int],
    tree: STRtree,
    cfg: SliverConfig,
) -> int | None:
    raw_cands: list[tuple[int, float, float, bool]] = []
    for idx_raw in tree.query(sliver_geom.buffer(1e-3), predicate="intersects"):
        idx = int(idx_raw)
        if idx == sliver_idx:
            continue
        ng = working[idx]
        if ng.is_empty:
            continue
        sb = shared_boundary_length(sliver_geom, ng)
        inter = extract_polygonal(sliver_geom.intersection(ng))
        ia = float(inter.area) if not inter.is_empty else 0.0
        if sb > 0.0 or ia > 0.0:
            raw_cands.append((idx, sb, ia, _is_standalone_sliver(ng, cfg)))

    if not raw_cands:
        return None

    non_slivers = [c for c in raw_cands if not c[3]]
    cands = non_slivers if non_slivers else raw_cands

    if cfg.merge_rule == "longest_shared_boundary":
        cands.sort(
            key=lambda item: (-item[1], -item[2], -working[item[0]].area, fids[item[0]])
        )
        return cands[0][0]

    if cfg.merge_rule == "largest_neighbor_area":
        cands.sort(
            key=lambda item: (-working[item[0]].area, -item[1], -item[2], fids[item[0]])
        )
        return cands[0][0]

    # same_attribute_class: match TEMA then JENIS, falling back to longest shared boundary
    sliver_attrs = attributes[sliver_idx]
    s_tema = _norm_attr(sliver_attrs.get("TEMA"))
    s_jenis = _norm_attr(sliver_attrs.get("JENIS"))

    scored: list[tuple[int, int, float, float, float, int, int]] = []
    for idx, sb, ia, _is_sliv in cands:
        n_attrs = attributes[idx]
        n_tema = _norm_attr(n_attrs.get("TEMA"))
        n_jenis = _norm_attr(n_attrs.get("JENIS"))
        tema_match = 1 if (s_tema and s_tema == n_tema) else 0
        jenis_match = 1 if (s_jenis and s_jenis == n_jenis) else 0
        scored.append(
            (
                -tema_match,
                -jenis_match,
                -sb,
                -ia,
                -float(working[idx].area),
                fids[idx],
                idx,
            )
        )
    scored.sort()
    return scored[0][-1]


def fix_slivers(
    source: str | Path | Any,
    config: SliverConfig | None = None,
) -> FixResult:
    """Clean disconnected multipart fragments and merge standalone slivers into neighbors."""
    cfg = config or SliverConfig()
    loaded = load_vector_source(source)
    ctx = build_metric_context(loaded.crs_wkt, loaded.geometries_2d)
    orig_metric = [ctx.to_metric(g) for g in loaded.geometries_2d]
    features_before = len(orig_metric)
    logs: list[str] = []

    if ctx.was_reprojected:
        logs.append(
            f"Normalized CRS to metric UTM for sliver cleaning ({loaded.layer_name})."
        )

    working: list[BaseGeometry] = []
    for geom_m in orig_metric:
        if geom_m is None or geom_m.is_empty:
            working.append(Polygon())
        elif geom_m.is_valid:
            working.append(extract_polygonal(geom_m))
        else:
            working.append(safe_make_valid(geom_m))

    modified_flags = [False] * len(working)
    multipart_cleaned = _clean_multipart_fragments(
        working=working,
        fids=loaded.fids,
        modified_flags=modified_flags,
        cfg=cfg,
    )

    standalone_merged = 0
    isolated_count = 0
    for _pass in range(2):
        sliver_queue = sorted(
            (
                (float(g.area), loaded.fids[idx], idx)
                for idx, g in enumerate(working)
                if not g.is_empty and _is_standalone_sliver(g, cfg)
            ),
            key=lambda item: (item[0], item[1]),
        )
        if not sliver_queue:
            break
        merged_this_pass = 0
        tree = STRtree(working)
        for _area, _fid, sliver_idx in sliver_queue:
            sliver_geom = working[sliver_idx]
            if sliver_geom.is_empty or not _is_standalone_sliver(sliver_geom, cfg):
                continue
            target_idx = _select_sliver_neighbor(
                sliver_idx=sliver_idx,
                sliver_geom=sliver_geom,
                working=working,
                attributes=loaded.attributes,
                fids=loaded.fids,
                tree=tree,
                cfg=cfg,
            )
            if target_idx is None:
                isolated_count += 1
                continue
            working[target_idx] = merge_adjacent_polygons(
                working[target_idx], sliver_geom
            )
            working[sliver_idx] = Polygon()
            modified_flags[target_idx] = True
            modified_flags[sliver_idx] = True
            standalone_merged += 1
            merged_this_pass += 1
            tree = STRtree(working)
        if merged_this_pass == 0:
            break

    if isolated_count > 0:
        logs.append(
            f"Preserved {isolated_count} isolated sliver feature(s) with no adjacent neighbor."
        )

    out_path, features_after, area_changed_m2 = commit_metric_geometries(
        loaded=loaded,
        ctx=ctx,
        orig_metric_geoms=orig_metric,
        fixed_metric_geoms=working,
        recalc_attributes=cfg.recalc_attributes,
        modified_flags=modified_flags,
    )

    rescan = scan_slivers(source, cfg)
    fixed_count = multipart_cleaned + standalone_merged
    logs.append(
        f"Sliver repair ({cfg.merge_rule}): multipart_cleaned={multipart_cleaned}, "
        f"standalone_merged={standalone_merged}, remaining={rescan.error_count}."
    )
    return FixResult(
        error_type=ERROR_SLIVER,
        fixed_count=fixed_count,
        features_before=features_before,
        features_after=features_after,
        remaining_errors=rescan.error_count,
        area_changed_m2=area_changed_m2,
        output_path=out_path,
        logs=logs,
    )
