"""Unit and integration tests for automated topology repair and in-place overwriting."""

from __future__ import annotations

import shutil
from pathlib import Path

import geopandas as gpd
import pytest
from shapely import ops
from shapely.geometry import Polygon, box

from tests.conftest import BENCHMARK_SHP, compute_sha256, temp_vector_data_test_shp
from vector_data_office_boy.core import (
    GapConfig,
    OverlapConfig,
    SelfIntersectionConfig,
    SliverConfig,
    fix_internal_gaps,
    fix_overlaps,
    fix_ring_self_intersections,
    fix_slivers,
    safe_make_valid,
    scan_internal_gaps,
    scan_overlaps,
    scan_ring_self_intersections,
    scan_slivers,
)


def _copy_shp_set(src_shp: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = src_shp.stem
    for file_path in sorted(src_shp.parent.glob(f"{stem}.*")):
        if file_path.name.lower().endswith((".sr.lock", ".lck")):
            continue
        shutil.copy2(file_path, dest_dir / file_path.name)
    return dest_dir / src_shp.name


def _union_exterior_polygons(gdf: gpd.GeoDataFrame) -> list[Polygon]:
    valid_geoms = [
        g if g.is_valid else safe_make_valid(g)
        for g in gdf.geometry
        if g is not None and not g.is_empty
    ]
    union_geom = ops.unary_union([g for g in valid_geoms if not g.is_empty])
    if union_geom.geom_type == "Polygon":
        return [Polygon(union_geom.exterior)]
    if union_geom.geom_type == "MultiPolygon":
        return [Polygon(p.exterior) for p in union_geom.geoms]
    return [
        Polygon(sub.exterior)
        for sub in union_geom.geoms
        if sub.geom_type == "Polygon"
    ]


def test_fix_ring_self_intersections_repairs_bowtie_and_spikes(
    synthetic_topology_shp: Path,
    tmp_path: Path,
) -> None:
    """Verify ring self-intersections and spikes are repaired and spike toggle works."""
    shp_with_spikes = _copy_shp_set(synthetic_topology_shp, tmp_path / "si_with_spikes")
    res = fix_ring_self_intersections(shp_with_spikes, SelfIntersectionConfig(detect_spikes=True))
    assert res.fixed_count == 2
    assert res.remaining_errors == 0
    assert res.features_before == 15
    assert res.features_after == 15

    gdf_fixed = gpd.read_file(shp_with_spikes)
    assert bool(gdf_fixed.geometry.iloc[0].is_valid)
    assert float(gdf_fixed.geometry.iloc[1].area) == pytest.approx(3600.0, abs=1e-2)

    shp_no_spikes = _copy_shp_set(synthetic_topology_shp, tmp_path / "si_no_spikes")
    res_no_spikes = fix_ring_self_intersections(
        shp_no_spikes,
        SelfIntersectionConfig(detect_spikes=False),
    )
    assert res_no_spikes.fixed_count == 1
    assert res_no_spikes.remaining_errors == 0
    assert scan_ring_self_intersections(shp_no_spikes, SelfIntersectionConfig(detect_spikes=True)).error_count == 1


def test_fix_overlaps_all_three_rules(synthetic_topology_shp: Path, tmp_path: Path) -> None:
    """Verify cut_hole_in_host, subtract_from_larger, and keep_lower_fid behaviors."""
    # 1. Option A default: cut_hole_in_host keeps all 15 features and carves hole in host
    shp_hole = _copy_shp_set(synthetic_topology_shp, tmp_path / "ov_hole")
    res_hole = fix_overlaps(shp_hole, OverlapConfig(overlap_rule="cut_hole_in_host"))
    assert res_hole.remaining_errors == 0
    assert res_hole.features_before == 15
    assert res_hole.features_after == 15

    gdf_hole = gpd.read_file(shp_hole)
    host_geom = gdf_hole.loc[gdf_hole["OBJECTID"] == 5, "geometry"].iloc[0]
    enclave_geom = gdf_hole.loc[gdf_hole["OBJECTID"] == 6, "geometry"].iloc[0]
    assert len(host_geom.interiors) == 1
    assert float(host_geom.area) == pytest.approx(10000.0 - 625.0, abs=1e-2)
    assert float(enclave_geom.area) == pytest.approx(625.0, abs=1e-2)

    # 2. subtract_from_larger trims larger partial polygon (OID 3: 2500 -> 2000) and host
    shp_larger = _copy_shp_set(synthetic_topology_shp, tmp_path / "ov_larger")
    res_larger = fix_overlaps(shp_larger, OverlapConfig(overlap_rule="subtract_from_larger"))
    assert res_larger.remaining_errors == 0
    assert res_larger.features_after == 15

    gdf_larger = gpd.read_file(shp_larger)
    oid3_area = float(gdf_larger.loc[gdf_larger["OBJECTID"] == 3, "geometry"].iloc[0].area)
    oid4_area = float(gdf_larger.loc[gdf_larger["OBJECTID"] == 4, "geometry"].iloc[0].area)
    assert oid3_area == pytest.approx(2000.0, abs=1e-2)
    assert oid4_area == pytest.approx(2000.0, abs=1e-2)

    # 3. keep_lower_fid keeps OID 3 (2500 m2), trims OID 4 (1500 m2), and deletes contained OID 6
    shp_lower = _copy_shp_set(synthetic_topology_shp, tmp_path / "ov_lower")
    res_lower = fix_overlaps(shp_lower, OverlapConfig(overlap_rule="keep_lower_fid"))
    assert res_lower.remaining_errors == 0
    assert res_lower.features_after == 14

    gdf_lower = gpd.read_file(shp_lower)
    assert 6 not in set(gdf_lower["OBJECTID"])
    oid3_low_area = float(gdf_lower.loc[gdf_lower["OBJECTID"] == 3, "geometry"].iloc[0].area)
    oid4_low_area = float(gdf_lower.loc[gdf_lower["OBJECTID"] == 4, "geometry"].iloc[0].area)
    assert oid3_low_area == pytest.approx(2500.0, abs=1e-2)
    assert oid4_low_area == pytest.approx(1500.0, abs=1e-2)


@pytest.mark.parametrize(
    ("merge_rule", "expected_grown_oid", "expected_area"),
    [
        ("longest_shared_boundary", 7, 6800.0),
        ("largest_neighbor_area", 7, 6800.0),
        ("smallest_neighbor_area", 9, 1600.0),
    ],
)
def test_fix_internal_gaps_rules_and_outer_boundary_preservation(
    synthetic_topology_shp: Path,
    tmp_path: Path,
    merge_rule: str,
    expected_grown_oid: int,
    expected_area: float,
) -> None:
    """Verify gap merge rules and strict outer dataset boundary preservation."""
    shp_gap = _copy_shp_set(synthetic_topology_shp, tmp_path / f"gap_{merge_rule}")
    before_gdf = gpd.read_file(shp_gap)
    before_exteriors = ops.unary_union(_union_exterior_polygons(before_gdf))

    res = fix_internal_gaps(shp_gap, GapConfig(merge_rule=merge_rule))
    assert res.fixed_count == 1
    assert res.remaining_errors == 0
    assert res.features_before == 15
    assert res.features_after == 15

    after_gdf = gpd.read_file(shp_gap)
    after_exteriors = ops.unary_union(_union_exterior_polygons(after_gdf))
    assert before_exteriors.symmetric_difference(after_exteriors).area < 1e-6

    grown_area = float(
        after_gdf.loc[after_gdf["OBJECTID"] == expected_grown_oid, "geometry"].iloc[0].area
    )
    assert grown_area == pytest.approx(expected_area, abs=1e-2)


@pytest.mark.parametrize(
    ("merge_rule", "expected_winner_oid"),
    [
        ("longest_shared_boundary", 2),  # 4 m shared edge with Pertanian (3000 m2)
        ("largest_neighbor_area", 1),    # 10,000 m2 Hutan (2 m shared edge)
        ("same_attribute_class", 3),     # Matching TEMA=Permukiman, JENIS=Perkotaan (1000 m2)
    ],
)
def test_fix_slivers_merge_rules_differentiation(
    tmp_path: Path,
    merge_rule: str,
    expected_winner_oid: int,
) -> None:
    """Verify longest_shared_boundary, largest_neighbor_area, and same_attribute_class rules."""
    sliver_shp = tmp_path / f"sliver_rules_{merge_rule}.shp"
    # Central 8 m2 sliver [10..12] x [10..14] (width=2, height=4 -> top/bottom=2m, left=4m, right=1m)
    rows = [
        {
            "OBJECTID": 1,
            "TEMA": "Hutan",
            "JENIS": "Hutan Lindung",
            "geometry": box(680010.0, 9300014.0, 680110.0, 9300114.0),  # 10,000 m2, shares 2m bottom
        },
        {
            "OBJECTID": 2,
            "TEMA": "Pertanian",
            "JENIS": "Sawah",
            "geometry": box(679960.0, 9300010.0, 680010.0, 9300070.0),  # 3,000 m2, shares 4m left
        },
        {
            "OBJECTID": 3,
            "TEMA": "Permukiman",
            "JENIS": "Perkotaan",
            "geometry": box(680012.0, 9300013.0, 680037.0, 9300053.0),  # 1,000 m2, shares 1m right
        },
        {
            "OBJECTID": 4,
            "TEMA": "Permukiman",
            "JENIS": "Perkotaan",
            "geometry": box(680010.0, 9300010.0, 680012.0, 9300014.0),  # 8 m2 micro-sliver
        },
    ]
    gpd.GeoDataFrame(rows, crs="EPSG:32748").to_file(sliver_shp, driver="ESRI Shapefile")

    before_areas = {
        row["OBJECTID"]: float(row["geometry"].area)
        for row in rows
    }
    res = fix_slivers(sliver_shp, SliverConfig(merge_rule=merge_rule))
    assert res.fixed_count == 1
    assert res.remaining_errors == 0
    assert res.features_after == 3

    after_gdf = gpd.read_file(sliver_shp)
    winner_area = float(
        after_gdf.loc[after_gdf["OBJECTID"] == expected_winner_oid, "geometry"].iloc[0].area
    )
    assert winner_area == pytest.approx(before_areas[expected_winner_oid] + 8.0, abs=1e-2)


def test_full_sequential_workflow_on_synthetic_and_benchmark_copy(
    synthetic_topology_shp: Path,
    tmp_path: Path,
) -> None:
    """Run SI -> OV -> GAP -> SLIV on synthetic data and isolated benchmark copy."""
    # Part 1: Synthetic dataset end-to-end
    syn_copy = _copy_shp_set(synthetic_topology_shp, tmp_path / "syn_full")
    for fix_fn in (
        fix_ring_self_intersections,
        fix_overlaps,
        fix_internal_gaps,
        fix_slivers,
    ):
        fix_res = fix_fn(syn_copy)
        assert fix_res.remaining_errors == 0

    assert scan_ring_self_intersections(syn_copy).error_count == 0
    assert scan_overlaps(syn_copy).error_count == 0
    assert scan_internal_gaps(syn_copy).error_count == 0
    assert scan_slivers(syn_copy).error_count == 0

    # Part 2: Benchmark dataset copy end-to-end + outer boundary + attribute recalculation
    before_hash = compute_sha256(BENCHMARK_SHP)
    bench_copy = temp_vector_data_test_shp(tmp_path / "bench_full")

    res_si = fix_ring_self_intersections(bench_copy)
    assert res_si.remaining_errors == 0
    assert res_si.features_before == 996
    assert res_si.features_after == 996

    res_ov = fix_overlaps(bench_copy)
    assert res_ov.remaining_errors == 0
    assert res_ov.features_after == 996

    before_gap_gdf = gpd.read_file(bench_copy)
    before_gap_ext = ops.unary_union(_union_exterior_polygons(before_gap_gdf))
    res_gap = fix_internal_gaps(bench_copy)
    assert res_gap.remaining_errors == 0
    after_gap_gdf = gpd.read_file(bench_copy)
    after_gap_ext = ops.unary_union(_union_exterior_polygons(after_gap_gdf))
    assert before_gap_ext.symmetric_difference(after_gap_ext).area < 1e-4

    res_sliv = fix_slivers(bench_copy)
    assert res_sliv.remaining_errors == 0
    assert res_sliv.features_after == 984

    assert scan_ring_self_intersections(bench_copy).error_count == 0
    assert scan_overlaps(bench_copy).error_count == 0
    assert scan_internal_gaps(bench_copy).error_count == 0
    assert scan_slivers(bench_copy).error_count == 0

    final_gdf = gpd.read_file(bench_copy)
    assert len(final_gdf) == 984
    for _, row in final_gdf.head(50).iterrows():
        geom_area = float(row.geometry.area)
        geom_len = float(row.geometry.length)
        assert float(row["Shape_Area"]) == pytest.approx(geom_area, rel=1e-4, abs=1e-3)
        assert float(row["Shape_Le_1"]) == pytest.approx(geom_len, rel=1e-4, abs=1e-3)
        assert float(row["LUAS_HA"]) == pytest.approx(round(geom_area / 10000.0, 4), abs=1e-4)

    assert compute_sha256(BENCHMARK_SHP) == before_hash


def test_recalc_attributes_false_preserves_original_values(
    synthetic_topology_shp: Path,
    tmp_path: Path,
) -> None:
    """Verify recalc_attributes=False leaves LUAS_HA, Shape_Area, and Shape_Le_1 unchanged."""
    syn_copy = _copy_shp_set(synthetic_topology_shp, tmp_path / "no_recalc")
    before_gdf = gpd.read_file(syn_copy)
    orig_oid7_area = float(before_gdf.loc[before_gdf["OBJECTID"] == 7, "Shape_Area"].iloc[0])

    fix_internal_gaps(syn_copy, GapConfig(recalc_attributes=False))
    after_gdf = gpd.read_file(syn_copy)
    after_oid7 = after_gdf.loc[after_gdf["OBJECTID"] == 7].iloc[0]

    assert float(after_oid7.geometry.area) == pytest.approx(6800.0, abs=1e-2)
    assert float(after_oid7["Shape_Area"]) == pytest.approx(orig_oid7_area, abs=1e-6)
