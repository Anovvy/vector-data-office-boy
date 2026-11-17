"""Automated repair for ring self-intersections and extreme boundary spikes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from vector_data_office_boy.core.detector import scan_ring_self_intersections
from vector_data_office_boy.core.geom_backend import (
    build_metric_context,
    find_boundary_spikes,
    prune_boundary_spikes,
    safe_make_valid,
)
from vector_data_office_boy.core.models import (
    ERROR_SELF_INTERSECTION,
    FixResult,
    SelfIntersectionConfig,
)
from vector_data_office_boy.core.overwrite_writer import (
    commit_metric_geometries,
    load_vector_source,
)


def fix_ring_self_intersections(
    source: str | Path | Any,
    config: SelfIntersectionConfig | None = None,
) -> FixResult:
    """Repair invalid self-intersecting rings and prune acute boundary spikes."""
    cfg = config or SelfIntersectionConfig()
    loaded = load_vector_source(source)
    ctx = build_metric_context(loaded.crs_wkt, loaded.geometries_2d)
    orig_metric = [ctx.to_metric(g) for g in loaded.geometries_2d]

    features_before = len(orig_metric)
    fixed_metric: list[BaseGeometry] = []
    modified_flags: list[bool] = []
    fixed_count = 0
    repaired_rings = 0
    pruned_spikes = 0
    logs: list[str] = []

    if ctx.was_reprojected:
        logs.append(
            f"Normalized CRS to metric UTM for self-intersection repair ({loaded.layer_name})."
        )

    for geom_m in orig_metric:
        if geom_m is None or geom_m.is_empty:
            fixed_metric.append(Polygon())
            modified_flags.append(False)
            continue

        was_invalid = not geom_m.is_valid
        repaired = (
            safe_make_valid(geom_m, min_area=cfg.min_part_area_m2)
            if was_invalid
            else geom_m
        )
        if was_invalid:
            repaired_rings += 1
            fixed_count += 1

        spikes_found = (
            find_boundary_spikes(
                repaired,
                max_spike_angle_deg=cfg.max_spike_angle_deg,
                max_spike_arm_m=cfg.max_spike_arm_m,
            )
            if cfg.detect_spikes and not repaired.is_empty
            else []
        )
        if spikes_found:
            repaired = prune_boundary_spikes(
                repaired,
                max_spike_angle_deg=cfg.max_spike_angle_deg,
                max_spike_arm_m=cfg.max_spike_arm_m,
                min_part_area_m2=cfg.min_part_area_m2,
            )
            pruned_spikes += len(spikes_found)
            fixed_count += len(spikes_found)

        if not repaired.is_empty and not repaired.is_valid:
            repaired = safe_make_valid(repaired, min_area=cfg.min_part_area_m2)

        fixed_metric.append(repaired)
        modified_flags.append(bool(was_invalid or spikes_found))

    out_path, features_after, area_changed_m2 = commit_metric_geometries(
        loaded=loaded,
        ctx=ctx,
        orig_metric_geoms=orig_metric,
        fixed_metric_geoms=fixed_metric,
        recalc_attributes=cfg.recalc_attributes,
        modified_flags=modified_flags,
    )

    rescan = scan_ring_self_intersections(source, cfg)
    logs.append(
        f"Self-intersection repair finished: {repaired_rings} invalid ring(s) repaired, "
        f"{pruned_spikes} spike(s) pruned, {rescan.error_count} issue(s) remaining."
    )
    return FixResult(
        error_type=ERROR_SELF_INTERSECTION,
        fixed_count=fixed_count,
        features_before=features_before,
        features_after=features_after,
        remaining_errors=rescan.error_count,
        area_changed_m2=area_changed_m2,
        output_path=out_path,
        logs=logs,
    )
