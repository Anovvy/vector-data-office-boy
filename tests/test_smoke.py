"""Baseline repository, automation script, and benchmark dataset smoke tests."""

from __future__ import annotations

import hashlib
import json
import py_compile
import re
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest

from scripts.agy_auto_maintainer import (
    _normalize_qgis_tag_version,
    parse_pip_audit_json,
)
from scripts.install_to_local_qgis import install_plugin_to_directory
from scripts.package_plugin import (
    build_plugin_archive,
    validate_plugin_archive,
    validate_plugin_source,
)

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
    """Verify required governance and documentation files exist and contain zero emojis."""
    required_files = [
        REPO_ROOT / "AGENT.md",
        REPO_ROOT / "README.md",
        REPO_ROOT / "CHANGELOG.md",
        REPO_ROOT / "LICENSE",
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "requirements-dev.txt",
        REPO_ROOT / ".gitignore",
    ]
    for file_path in required_files:
        assert file_path.exists(), f"Missing required file: {file_path}"
        text = file_path.read_text(encoding="utf-8")
        assert len(text.strip()) > 0, f"Required file is empty: {file_path.name}"
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
        "scripts/package_plugin.py",
        "scripts/install_to_local_qgis.py",
        "scripts/agy_auto_maintainer.py",
    ],
)
def test_scripts_compile_cleanly_and_have_no_emojis(script_rel_path: str) -> None:
    """Verify all automation Python scripts compile cleanly and contain zero emojis."""
    target = REPO_ROOT / script_rel_path
    assert target.exists(), f"Missing script: {target}"
    py_compile.compile(str(target), doraise=True)
    text = target.read_text(encoding="utf-8")
    assert not EMOJI_PATTERN.search(text), f"Emoji detected in {script_rel_path}"


def test_package_plugin_builds_clean_zip_and_checksum(tmp_path: Path) -> None:
    """Verify package_plugin builds a valid QGIS archive without forbidden files."""
    meta = validate_plugin_source(expected_tag="v1.0.1")
    assert meta["version"] == "1.0.1"

    archive_path, checksum_path = build_plugin_archive(output_dir=tmp_path)
    validate_plugin_archive(archive_path)

    checksum_line = checksum_path.read_text(encoding="utf-8").strip()
    assert checksum_line.endswith("  vector_data_office_boy.zip")
    digest_hex = checksum_line.split()[0]
    assert digest_hex == _sha256(archive_path)

    with zipfile.ZipFile(archive_path, "r") as zf:
        entries = zf.namelist()
    assert all(entry.startswith("vector_data_office_boy/") for entry in entries)
    assert not any("__pycache__" in entry or entry.endswith(".pyc") for entry in entries)


def test_install_to_local_qgis_copies_without_pycache(tmp_path: Path) -> None:
    """Verify install_plugin_to_directory copies plugin cleanly into target directory."""
    dest_dir = tmp_path / "profiles" / "default" / "python" / "plugins" / "vector_data_office_boy"
    installed = install_plugin_to_directory(target_dir=dest_dir)
    assert installed.is_dir()
    assert (installed / "metadata.txt").is_file()
    assert (installed / "__init__.py").is_file()
    assert not list(installed.rglob("__pycache__"))
    assert not list(installed.rglob("*.pyc"))


def test_auto_maintainer_helpers(tmp_path: Path) -> None:
    """Verify pip-audit JSON parsing and QGIS tag normalization helpers."""
    assert _normalize_qgis_tag_version("final-3_44_15") == "3.44.15"
    assert _normalize_qgis_tag_version("final-3_40_0") == "3.40.0"

    audit_file = tmp_path / "pip_audit.json"
    audit_file.write_text(
        json.dumps(
            {
                "dependencies": [
                    {"name": "requests", "version": "2.31.0", "vulns": [{"id": "CVE-2024-0001"}]},
                    {"name": "shapely", "version": "2.1.0", "vulns": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    summary = parse_pip_audit_json(audit_file)
    assert summary["status"] == "ok"
    assert summary["total_vulns"] == 1
    assert len(summary["vulnerable_packages"]) == 1
    assert summary["vulnerable_packages"][0]["name"] == "requests"
