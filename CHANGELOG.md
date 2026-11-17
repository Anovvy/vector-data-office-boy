# Changelog

All notable changes to **Vector Data Office Boy** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-11-17

### Added
- **Core Spatial Topology Engine (`vector_data_office_boy/core/`)**:
  - Ring self-intersection scanner and repairer (`make_valid` + polygonal extraction + configurable acute boundary spike detection and pruning).
  - Overlap polygon scanner and resolver supporting `cut_hole_in_host` (Option A non-destructive hole carving for 100% contained enclaves), `subtract_from_larger`, `keep_lower_fid`, micro-overlap toggle, and precision coverage noding.
  - Internal gap scanner and filler operating strictly on `unary_union.interiors` so outer dataset boundaries (`exterior`) are never altered, with `longest_shared_boundary`, `largest_neighbor_area`, and `smallest_neighbor_area` merge rules.
  - Sliver polygon and multipart fragment scanner and cleaner supporting absolute area threshold, Polsby-Popper compactness threshold capped by maximum area, minimum `MultiPolygon` part area pruning, and `same_attribute_class` (`TEMA`/`JENIS`) merging.
  - Automatic metric UTM CRS normalization for geographic (`EPSG:4326`) or non-meter datasets, `PolygonZ` Z-dimension preservation, atomic staged file overwriting, and automatic recalculation of `LUAS_HA`, `Shape_Area`, and `Shape_Le_1`.
  - Temporary memory layer creation (`Error_Ring_Self_Intersection`, `Error_Overlap_Polygon`, `Error_Internal_Gap`, `Error_Sliver_Polygon`) with single-symbol fill renderers (`#FF8C00`, `#FF2400`, `#9B30FF`, `#FFD700`) and strict 11-column attribute schema.
- **Qt Designer UI, PyQGIS Controller, and Processing Provider**:
  - Standard Qt5 `.ui` dialog (`office_boy_dialog_base.ui`) compatible with standalone Qt Designer (zero `Qgs*` custom widgets).
  - Non-modal `OfficeBoyDialog` with `Parameters` and `Log` tabs, live HTML **Parameter Help** panel (`PARAMETER_HELP_DOCS`), non-closing per-section **Scan** and **Fix It** buttons, summary table, console log, and layer reload/repaint hooks.
  - QGIS Processing provider (`VectorDataOfficeBoyProvider`) registering 4 Scan algorithms and 4 Fix algorithms.
- **QA Test Suite, Packaging, Installer, CI/CD, and Documentation**:
  - Comprehensive Pytest suite (`tests/conftest.py`, `tests/test_detector.py`, `tests/test_fixers.py`, `tests/test_symbology_and_plugin.py`, `tests/test_smoke.py`) validating synthetic topology fixtures, isolated temporary copies of `data_test/vector_data_test.shp` with SHA-256 immutability assertions, and PyQGIS integration.
  - Plugin packager (`scripts/package_plugin.py`) generating `dist/vector_data_office_boy.zip` and `dist/SHA256SUMS.txt` with `--validate` and `--check-tag` gates.
  - Cross-platform local QGIS profile installer (`scripts/install_to_local_qgis.py`) with `--verify` offscreen PyQGIS smoke check.
  - Antigravity SDK PR QA Reviewer (`.github/workflows/pr_qa_review.yml`), weekly Auto-Maintainer cron workflow (`.github/workflows/auto_maintenance_agent.yml`), and automated GitHub Release CD workflow (`.github/workflows/release.yml`).
  - Complete English documentation in `README.md` and governance rules in `AGENT.md`.
