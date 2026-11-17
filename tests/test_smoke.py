"""Baseline repository and benchmark dataset smoke tests."""

from __future__ import annotations

import hashlib
import py_compile
import re
from pathlib import Path

import geopandas as gpd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_TEST_DIR = REPO_ROOT / "data_test"
VECTOR_DATA_TEST_SHP = DATA_TEST_DIR / "vector_data_test.shp"

EMOJI_PATTERN = re.compile(
    "["
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa70-\U0001faff"
    "\u2600-\u26ff"
    "\u2700-\u27bf"
    "]+",
    flags=re.UNICODE,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_required_governance_files_exist_and_have_no_emojis() -> None:
    """Verify required governance files exist and obey the zero-emoji rule."""
    required_files = [
        REPO_ROOT / "AGENT.md",
        REPO_ROOT / "CHANGELOG.md",
        REPO_ROOT / "LICENSE",
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "requirements-dev.txt",
        REPO_ROOT / ".gitignore",
    ]
    for file_path in required_files:
        assert file_path.exists(), f"Missing required file: {file_path}"
        text = file_path.read_text(encoding="utf-8")
        assert not EMOJI_PATTERN.search(text), f"Emoji detected in {file_path.name}"


def test_no_temporary_lock_files_in_data_test() -> None:
    """Ensure no ArcGIS lock files remain inside data_test."""
    lock_files = list(DATA_TEST_DIR.glob("*.sr.lock")) + list(DATA_TEST_DIR.glob("*.lck"))
    assert lock_files == [], f"Unexpected shapefile lock files found: {lock_files}"


def test_benchmark_shapefile_loads_cleanly_and_is_unmodified() -> None:
    """Verify data_test/vector_data_test.shp loads expected schema without mutation."""
    required_sidecars = [".shp", ".shx", ".dbf", ".prj", ".cpg"]
    for ext in required_sidecars:
        sidecar = VECTOR_DATA_TEST_SHP.with_suffix(ext)
        assert sidecar.exists(), f"Missing benchmark shapefile sidecar: {sidecar.name}"

    before_hash = _sha256(VECTOR_DATA_TEST_SHP)
    gdf = gpd.read_file(VECTOR_DATA_TEST_SHP)

    assert len(gdf) == 996
    assert gdf.crs is not None
    assert gdf.crs.to_epsg() == 32748
    assert "OBJECTID" in gdf.columns
    assert "geometry" in gdf.columns
    assert _sha256(VECTOR_DATA_TEST_SHP) == before_hash


@pytest.mark.parametrize(
    "script_rel_path",
    [
        "scripts/agy_pr_reviewer.py",
    ],
)
def test_scripts_compile_cleanly(script_rel_path: str) -> None:
    """Verify automation Python scripts have valid syntax."""
    target = REPO_ROOT / script_rel_path
    assert target.exists(), f"Missing script: {target}"
    py_compile.compile(str(target), doraise=True)
