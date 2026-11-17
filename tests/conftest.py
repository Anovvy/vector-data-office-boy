"""Shared Pytest fixtures and isolated shapefile helpers for Vector Data Office Boy."""

from __future__ import annotations

import hashlib
import math
import shutil
from collections.abc import Iterator
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import MultiPolygon, Polygon, box

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_TEST_DIR = REPO_ROOT / "data_test"
BENCHMARK_SHP = DATA_TEST_DIR / "vector_data_test.shp"


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def temp_vector_data_test_shp(tmp_path: Path) -> Path:
    """Copy data_test/vector_data_test.* into tmp_path and verify source immutability."""
    if not BENCHMARK_SHP.is_file():
        raise FileNotFoundError(f"Benchmark shapefile not found: {BENCHMARK_SHP}")

    before_hash = compute_sha256(BENCHMARK_SHP)
    dest_dir = tmp_path / "benchmark_copy"
    dest_dir.mkdir(parents=True, exist_ok=True)

    for src_file in sorted(DATA_TEST_DIR.glob("vector_data_test.*")):
        if src_file.name.lower().endswith((".sr.lock", ".lck")):
            continue
        shutil.copy2(src_file, dest_dir / src_file.name)

    after_hash = compute_sha256(BENCHMARK_SHP)
    assert after_hash == before_hash, "Original benchmark shapefile SHA-256 changed during copy!"
    return dest_dir / "vector_data_test.shp"


@pytest.fixture
def isolated_benchmark_shp(tmp_path: Path) -> Iterator[Path]:
    """Provide an isolated temp copy of data_test/vector_data_test.shp and assert immutability."""
    before_hash = compute_sha256(BENCHMARK_SHP)
    copied_shp = temp_vector_data_test_shp(tmp_path)
    yield copied_shp
    assert compute_sha256(BENCHMARK_SHP) == before_hash, (
        "Original data_test/vector_data_test.shp was modified during test execution!"
    )


def _build_synthetic_features() -> list[dict[str, object]]:
    """Construct deterministic EPSG:32748 test polygons for all 4 topology classes."""
    # Block A: Ring self-intersection (asymmetric figure-8 bow-tie, area > 150 m2)
    # + acute 0.4 deg kickback spike (2.0 m arm)
    bowtie_poly = Polygon(
        [
            (680000.0, 9300000.0),
            (680060.0, 9300000.0),
            (680000.0, 9300040.0),
            (680020.0, 9300040.0),
            (680000.0, 9300000.0),
        ]
    )

    arm_m = 2.0
    half_angle_rad = math.radians(0.2)  # 0.4 deg total interior vertex angle
    spike_dx = arm_m * math.sin(half_angle_rad)
    spike_dy = arm_m * math.cos(half_angle_rad)
    spike_poly = Polygon(
        [
            (680100.0, 9300000.0),
            (680160.0, 9300000.0),
            (680160.0, 9300060.0),
            (680130.0 + spike_dx, 9300060.0),
            (680130.0, 9300060.0 + spike_dy),
            (680130.0 - spike_dx, 9300060.0),
            (680100.0, 9300060.0),
            (680100.0, 9300000.0),
        ]
    )

    # Block B: Partial boundary overlap (500 m2) + 100% contained enclave (625 m2 inside 10,000 m2)
    partial_left = box(681000.0, 9300000.0, 681050.0, 9300050.0)
    partial_right = box(681040.0, 9300000.0, 681080.0, 9300050.0)
    host_poly = box(681150.0, 9300000.0, 681250.0, 9300100.0)
    contained_poly = box(681180.0, 9300030.0, 681205.0, 9300055.0)

    # Block C: Rectangular coverage [682000..682120] x [9300000..9300120] around 800 m2 internal gap
    gap_bottom = box(682000.0, 9300000.0, 682120.0, 9300050.0)  # 6000 m2, 40m shared edge
    gap_top = box(682000.0, 9300070.0, 682120.0, 9300120.0)     # 6000 m2, 40m shared edge
    gap_left = box(682000.0, 9300050.0, 682040.0, 9300070.0)    # 800 m2, 20m shared edge
    gap_right = box(682080.0, 9300050.0, 682120.0, 9300070.0)   # 800 m2, 20m shared edge

    # Block D: Micro-sliver (8 m2), thin sliver (100 m2, PP ~ 0.0308), and MultiPolygon w/ 0.64 m2 part
    neighbor_permukiman = box(683000.0, 9300000.0, 683050.0, 9300100.0)  # 5000 m2
    micro_sliver = box(683050.0, 9300000.0, 683052.0, 9300004.0)         # 8 m2 <= 10 m2
    neighbor_hutan = box(683100.0, 9300000.0, 683200.0, 9300100.0)       # 10000 m2
    thin_sliver = box(683100.0, 9300100.0, 683200.0, 9300101.0)          # 100 m2, PP=0.0308
    multipart_poly = MultiPolygon(
        [
            box(683300.0, 9300000.0, 683350.0, 9300050.0),  # 2500 m2 main body
            box(683360.0, 9300000.0, 683360.8, 9300000.8),  # 0.64 m2 disconnected micro-part
        ]
    )

    specs = [
        (1, "Pertanian", "Sawah", bowtie_poly),
        (2, "Pertanian", "Ladang", spike_poly),
        (3, "Hutan", "Hutan Produksi", partial_left),
        (4, "Pertanian", "Perkebunan", partial_right),
        (5, "Hutan", "Hutan Lindung", host_poly),
        (6, "Permukiman", "Perdesaan", contained_poly),
        (7, "Pertanian", "Sawah", gap_bottom),
        (8, "Pertanian", "Sawah", gap_top),
        (9, "Pertanian", "Ladang", gap_left),
        (10, "Pertanian", "Ladang", gap_right),
        (11, "Permukiman", "Perkotaan", neighbor_permukiman),
        (12, "Permukiman", "Perkotaan", micro_sliver),
        (13, "Hutan", "Hutan Lindung", neighbor_hutan),
        (14, "Hutan", "Hutan Lindung", thin_sliver),
        (15, "Industri", "Kawasan Industri", multipart_poly),
    ]

    rows: list[dict[str, object]] = []
    for oid, tema, jenis, geom in specs:
        area_m2 = float(geom.area)
        perim_m = float(geom.length)
        rows.append(
            {
                "OBJECTID": oid,
                "TEMA": tema,
                "JENIS": jenis,
                "LUAS_HA": round(area_m2 / 10000.0, 4),
                "Shape_Area": area_m2,
                "Shape_Le_1": perim_m,
                "geometry": geom,
            }
        )
    return rows


@pytest.fixture
def synthetic_topology_shp(tmp_path: Path) -> Path:
    """Create a projected EPSG:32748 shapefile with all 4 topology error classes."""
    rows = _build_synthetic_features()
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:32748")
    out_dir = tmp_path / "synthetic"
    out_dir.mkdir(parents=True, exist_ok=True)
    shp_path = out_dir / "synthetic_topology.shp"
    gdf.to_file(shp_path, driver="ESRI Shapefile")
    return shp_path
