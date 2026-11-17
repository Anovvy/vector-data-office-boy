"""Install and verify Vector Data Office Boy in the local QGIS user profile."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR_NAME = "vector_data_office_boy"
PLUGIN_SRC_DIR = REPO_ROOT / PLUGIN_DIR_NAME

EXPECTED_ALGORITHM_NAMES: tuple[str, ...] = (
    "scan_ring_self_intersections",
    "scan_overlaps",
    "scan_internal_gaps",
    "scan_slivers",
    "fix_ring_self_intersections",
    "fix_overlaps",
    "fix_internal_gaps",
    "fix_slivers",
)


def get_qgis_plugins_dir(profile: str = "default") -> Path:
    """Resolve the local QGIS3 profile python/plugins directory for the current OS."""
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", "").strip()
        base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        return base / "QGIS" / "QGIS3" / "profiles" / profile / "python" / "plugins"

    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "QGIS"
            / "QGIS3"
            / "profiles"
            / profile
            / "python"
            / "plugins"
        )

    xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg_data_home) if xdg_data_home else (Path.home() / ".local" / "share")
    return base / "QGIS" / "QGIS3" / "profiles" / profile / "python" / "plugins"


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        lowered = name.lower()
        if name in {"__pycache__", ".DS_Store", "Thumbs.db", ".pytest_cache", ".ruff_cache"}:
            ignored.add(name)
        elif lowered.endswith((".pyc", ".pyo", ".sr.lock", ".lck")):
            ignored.add(name)
    return ignored


def install_plugin_to_directory(
    source_dir: Path = PLUGIN_SRC_DIR,
    target_dir: Path | None = None,
    profile: str = "default",
) -> Path:
    """Copy the plugin package cleanly into the target QGIS plugins directory."""
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Plugin source directory not found: {source_dir}")

    dest_dir = target_dir if target_dir is not None else (get_qgis_plugins_dir(profile) / PLUGIN_DIR_NAME)
    dest_dir = dest_dir.resolve()
    if dest_dir == source_dir.resolve():
        raise ValueError("Destination plugin directory cannot be the repository source folder.")

    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    shutil.copytree(source_dir, dest_dir, ignore=_copy_ignore)
    return dest_dir


def verify_installed_plugin(installed_dir: Path) -> None:
    """Import and verify the installed plugin, dialog, and 8 Processing algorithms in PyQGIS."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from qgis.core import QgsApplication

    # Ensure imports resolve from the installed QGIS profile directory rather than repo root.
    repo_str = str(REPO_ROOT.resolve())
    sys.path = [p for p in sys.path if Path(p or ".").resolve() != Path(repo_str)]
    plugins_parent = str(installed_dir.parent.resolve())
    if plugins_parent not in sys.path:
        sys.path.insert(0, plugins_parent)

    for mod_name in list(sys.modules):
        if mod_name == PLUGIN_DIR_NAME or mod_name.startswith(f"{PLUGIN_DIR_NAME}."):
            del sys.modules[mod_name]

    import vector_data_office_boy  # noqa: E402
    from vector_data_office_boy import classFactory  # noqa: E402
    from vector_data_office_boy.processing_provider import VectorDataOfficeBoyProvider  # noqa: E402
    from vector_data_office_boy.ui import OfficeBoyDialog  # noqa: E402
    from vector_data_office_boy.ui.office_boy_dialog import PARAMETER_HELP_DOCS  # noqa: E402

    imported_file = Path(vector_data_office_boy.__file__).resolve()
    if not imported_file.is_relative_to(installed_dir.resolve()):
        raise RuntimeError(
            f"Plugin imported from unexpected location {imported_file}; expected under {installed_dir}"
        )

    qgs = QgsApplication([], False)
    qgs.initQgis()
    plugin = None
    dialog = None
    try:
        plugin = classFactory(None)
        plugin.initProcessing()

        registry = QgsApplication.processingRegistry()
        provider = (
            registry.providerById("vector_data_office_boy")
            if registry is not None
            else None
        )
        if provider is None:
            provider = VectorDataOfficeBoyProvider()
            provider.loadAlgorithms()

        alg_names = [alg.name() for alg in provider.algorithms()]
        if len(alg_names) != len(EXPECTED_ALGORITHM_NAMES):
            raise RuntimeError(
                f"Expected {len(EXPECTED_ALGORITHM_NAMES)} Processing algorithms, found {len(alg_names)}: {alg_names}"
            )
        missing_algs = [name for name in EXPECTED_ALGORITHM_NAMES if name not in alg_names]
        if missing_algs:
            raise RuntimeError(f"Missing expected Processing algorithms: {missing_algs}")

        dialog = OfficeBoyDialog(iface=None)
        required_widgets = (
            "tabMain",
            "tabParameters",
            "tabLog",
            "cmbInputLayer",
            "btnRefreshLayers",
            "chkRecalcAttrs",
            "btnScanSelfIntersect",
            "btnFixSelfIntersect",
            "btnScanOverlap",
            "btnFixOverlap",
            "btnScanGap",
            "btnFixGap",
            "btnScanSliver",
            "btnFixSliver",
            "txtParameterHelp",
            "tblSummary",
            "txtLog",
            "progressBar",
            "btnClose",
        )
        for widget_name in required_widgets:
            if not hasattr(dialog, widget_name):
                raise RuntimeError(f"OfficeBoyDialog missing expected widget '{widget_name}'.")

        if len(PARAMETER_HELP_DOCS) < 20:
            raise RuntimeError(
                f"Unexpected PARAMETER_HELP_DOCS count ({len(PARAMETER_HELP_DOCS)})."
            )

        print(
            f"Verified QGIS plugin at '{installed_dir}': "
            f"classFactory OK, OfficeBoyDialog OK ({len(PARAMETER_HELP_DOCS)} help pages), "
            f"Processing provider OK ({len(alg_names)} algorithms)."
        )
    finally:
        if dialog is not None:
            dialog.cleanup()
            dialog.close()
            dialog.deleteLater()
        if plugin is not None:
            plugin.unload()
        qgs.exitQgis()


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for installing and optionally verifying the QGIS plugin."""
    parser = argparse.ArgumentParser(
        description="Install Vector Data Office Boy into the local QGIS profile plugins directory."
    )
    parser.add_argument(
        "--profile",
        default="default",
        help="QGIS user profile name (default: 'default').",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help="Optional explicit destination directory for vector_data_office_boy.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify the installed plugin inside an offscreen PyQGIS session.",
    )
    args = parser.parse_args(argv)

    try:
        installed_dir = install_plugin_to_directory(
            source_dir=PLUGIN_SRC_DIR,
            target_dir=args.target_dir,
            profile=args.profile,
        )
        print(f"Installed plugin to: {installed_dir}")
        if args.verify:
            verify_installed_plugin(installed_dir)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
