"""Unit and integration tests for topology error scanners."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import geopandas as gpd
import pytest
import shapely
from shapely.geometry import box

from tests.conftest import BENCHMARK_SHP, compute_sha256, temp_vector_data_test_shp
from vector_data_office_boy.core import (
    ERROR_GAP,
    ERROR_LAYER_NAMES,
    ERROR_OVERLAP,
    ERROR_SELF_INTERSECTION,
    ERROR_SLIVER,
    SCAN_ATTRIBUTE_FIELDS,
    GapConfig,
    OverlapConfig,
    SelfIntersectionConfig,
    SliverConfig,
    TopologyErrorRecord,
    scan_internal_gaps,
    scan_overlaps,
    scan_ring_self_intersections,
    scan_slivers,
)


def _assert_valid_scan_records(records: list[TopologyErrorRecord], expected_type: str) -> None:
    """Assert every TopologyErrorRecord strictly follows the 11-column schema."""
    expected_11 = (
        "error_id",
        "error_type",
        "severity",
        "fid_1",
        "fid_2",
        "oid_ref",
        "area_m2",
        "perim_m",
        "pp_index",
        "error_x",
        "error_y",
    )
    assert SCAN_ATTRIBUTE_FIELDS == expected_11
    assert "details" not in SCAN_ATTRIBUTE_FIELDS
    assert "fix_method" not in SCAN_ATTRIBUTE_FIELDS

    record_field_names = {f.name for f in dataclasses.fields(TopologyErrorRecord)}
    assert "details" not in record_field_names
    assert "fix_method" not in record_field_names
    assert set(SCAN_ATTRIBUTE_FIELDS).issubset(record_field_names)

    for idx, rec in enumerate(records, start=1):
        assert rec.error_id == idx
        assert rec.error_type == expected_type
        assert rec.severity in {"CRITICAL", "HIGH", "MEDIUM"}
        assert isinstance(rec.fid_1, int)
        assert isinstance(rec.fid_2, str)
        assert isinstance(rec.oid_ref, str) and len(rec.oid_ref) > 0
        assert rec.area_m2 > 0.0
        assert rec.perim_m > 0.0
        assert 0.0 <= rec.pp_index <= 1.0
        assert not hasattr(rec, "details")
        assert not hasattr(rec, "fix_method")
        geom = shapely.from_wkb(rec.geometry_wkb)
        assert not geom.is_empty
        assert geom.geom_type in {"Polygon", "MultiPolygon"}


def test_scan_synthetic_all_categories(synthetic_topology_shp: Path) -> None:
    """Verify all 4 scanners detect exact deterministic issues on synthetic_topology_shp."""
    si_res = scan_ring_self_intersections(synthetic_topology_shp)
    assert si_res.error_type == ERROR_SELF_INTERSECTION
    assert si_res.layer_name == ERROR_LAYER_NAMES[ERROR_SELF_INTERSECTION]
    assert si_res.error_count == 2
    assert {rec.severity for rec in si_res.records} == {"CRITICAL", "MEDIUM"}
    _assert_valid_scan_records(si_res.records, ERROR_SELF_INTERSECTION)

    ov_res = scan_overlaps(synthetic_topology_shp)
    assert ov_res.error_type == ERROR_OVERLAP
    assert ov_res.layer_name == ERROR_LAYER_NAMES[ERROR_OVERLAP]
    assert ov_res.error_count == 2
    assert ov_res.total_area_m2 == pytest.approx(500.0 + 625.0, rel=1e-4)
    _assert_valid_scan_records(ov_res.records, ERROR_OVERLAP)

    gap_res = scan_internal_gaps(synthetic_topology_shp)
    assert gap_res.error_type == ERROR_GAP
    assert gap_res.layer_name == ERROR_LAYER_NAMES[ERROR_GAP]
    assert gap_res.error_count == 1
    assert gap_res.total_area_m2 == pytest.approx(800.0, rel=1e-4)
    _assert_valid_scan_records(gap_res.records, ERROR_GAP)

    sliv_res = scan_slivers(synthetic_topology_shp)
    assert sliv_res.error_type == ERROR_SLIVER
    assert sliv_res.layer_name == ERROR_LAYER_NAMES[ERROR_SLIVER]
    assert sliv_res.error_count == 3
    assert sliv_res.total_area_m2 == pytest.approx(8.0 + 100.0 + 0.64, rel=1e-4)
    _assert_valid_scan_records(sliv_res.records, ERROR_SLIVER)


def test_scan_benchmark_copy_all_categories(tmp_path: Path) -> None:
    """Scan an isolated temporary copy of data_test/vector_data_test.shp safely."""
    before_hash = compute_sha256(BENCHMARK_SHP)
    copied_shp = temp_vector_data_test_shp(tmp_path)

    si_res = scan_ring_self_intersections(copied_shp)
    assert si_res.error_count == 4
    _assert_valid_scan_records(si_res.records, ERROR_SELF_INTERSECTION)

    ov_res = scan_overlaps(copied_shp)
    assert ov_res.error_count == 11
    _assert_valid_scan_records(ov_res.records, ERROR_OVERLAP)

    gap_res = scan_internal_gaps(copied_shp)
    assert gap_res.error_count == 4
    _assert_valid_scan_records(gap_res.records, ERROR_GAP)

    sliv_res = scan_slivers(copied_shp)
    assert sliv_res.error_count == 14
    _assert_valid_scan_records(sliv_res.records, ERROR_SLIVER)

    assert compute_sha256(BENCHMARK_SHP) == before_hash


def test_detect_spikes_toggle(synthetic_topology_shp: Path) -> None:
    """Verify SelfIntersectionConfig.detect_spikes controls boundary spike flagging."""
    res_with_spikes = scan_ring_self_intersections(
        synthetic_topology_shp,
        SelfIntersectionConfig(detect_spikes=True),
    )
    assert res_with_spikes.error_count == 2

    res_no_spikes = scan_ring_self_intersections(
        synthetic_topology_shp,
        SelfIntersectionConfig(detect_spikes=False),
    )
    assert res_no_spikes.error_count == 1
    assert res_no_spikes.records[0].severity == "CRITICAL"


def test_include_micro_overlaps_toggle(tmp_path: Path) -> None:
    """Verify OverlapConfig.include_micro_overlaps overrides min_overlap_area_m2."""
    micro_overlap_shp = tmp_path / "micro_overlap.shp"
    # Overlap width 0.00001 m x height 2.0 m = 0.00002 m2 (< default 0.0001 m2)
    gdf = gpd.GeoDataFrame(
        [
            {"OBJECTID": 1, "geometry": box(680000.0, 9300000.0, 680010.00001, 9300002.0)},
            {"OBJECTID": 2, "geometry": box(680010.0, 9300000.0, 680020.0, 9300002.0)},
        ],
        crs="EPSG:32748",
    )
    gdf.to_file(micro_overlap_shp, driver="ESRI Shapefile")

    res_filtered = scan_overlaps(
        micro_overlap_shp,
        OverlapConfig(min_overlap_area_m2=0.0001, include_micro_overlaps=False),
    )
    assert res_filtered.error_count == 0

    res_included = scan_overlaps(
        micro_overlap_shp,
        OverlapConfig(min_overlap_area_m2=0.0001, include_micro_overlaps=True),
    )
    assert res_included.error_count == 1
    assert res_included.records[0].area_m2 == pytest.approx(0.00002, rel=1e-2)


def test_metric_crs_auto_reprojection_for_epsg4326(synthetic_topology_shp: Path, tmp_path: Path) -> None:
    """Verify EPSG:4326 degree inputs are auto-projected to metric UTM for accurate areas."""
    gdf_utm = gpd.read_file(synthetic_topology_shp)
    gdf_4326 = gdf_utm.to_crs("EPSG:4326")
    shp_4326 = tmp_path / "synthetic_4326.shp"
    gdf_4326.to_file(shp_4326, driver="ESRI Shapefile")

    si_res = scan_ring_self_intersections(shp_4326, SelfIntersectionConfig(detect_spikes=True))
    assert si_res.error_count == 2
    assert any("Normalized" in msg for msg in si_res.logs)

    ov_res = scan_overlaps(shp_4326)
    assert ov_res.error_count == 2
    assert ov_res.total_area_m2 == pytest.approx(1125.0, rel=1e-2)
    # Error coordinates and geometries must be returned in source CRS (WGS84 degrees)
    assert 100.0 <= ov_res.records[0].error_x <= 115.0
    assert -10.0 <= ov_res.records[0].error_y <= 0.0

    gap_res = scan_internal_gaps(shp_4326, GapConfig())
    assert gap_res.error_count == 1
    assert gap_res.total_area_m2 == pytest.approx(800.0, rel=1e-2)

    sliv_res = scan_slivers(shp_4326, SliverConfig())
    assert sliv_res.error_count == 3
    assert sliv_res.total_area_m2 == pytest.approx(108.64, rel=2e-2)
