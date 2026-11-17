"""Package and validate the Vector Data Office Boy QGIS plugin ZIP archive."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import os
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR_NAME = "vector_data_office_boy"
PLUGIN_SRC_DIR = REPO_ROOT / PLUGIN_DIR_NAME
DEFAULT_DIST_DIR = REPO_ROOT / "dist"
ARCHIVE_NAME = "vector_data_office_boy.zip"
CHECKSUM_FILENAME = "SHA256SUMS.txt"
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024  # 25 MB QGIS Plugin Repository safety limit

REQUIRED_METADATA_KEYS: tuple[str, ...] = (
    "name",
    "qgisMinimumVersion",
    "description",
    "version",
    "author",
    "email",
    "about",
    "tracker",
    "repository",
    "license",
    "icon",
)

FORBIDDEN_DIR_NAMES: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        "tests",
        "data_test",
        "build",
        "dist",
    }
)

FORBIDDEN_FILE_NAMES: frozenset[str] = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
    }
)

FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    ".pyc",
    ".pyo",
    ".sr.lock",
    ".lck",
)


def _is_forbidden_path(rel_path: Path) -> bool:
    """Return True if a path matches any forbidden directory, filename, or suffix."""
    for part in rel_path.parts[:-1]:
        if part in FORBIDDEN_DIR_NAMES:
            return True
    name = rel_path.name
    if name in FORBIDDEN_FILE_NAMES or name in FORBIDDEN_DIR_NAMES:
        return True
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES)


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_plugin_metadata(plugin_dir: Path = PLUGIN_SRC_DIR) -> dict[str, str]:
    """Parse [general] keys from metadata.txt without interpolation surprises."""
    metadata_path = plugin_dir / "metadata.txt"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Missing plugin metadata file: {metadata_path}")

    parser = configparser.RawConfigParser()
    parser.optionxform = str  # preserve exact key casing for QGIS repository checks
    parser.read(metadata_path, encoding="utf-8")
    if not parser.has_section("general"):
        raise ValueError(f"metadata.txt is missing required [general] section: {metadata_path}")
    return {key: value.strip() for key, value in parser.items("general")}


def validate_plugin_source(
    plugin_dir: Path = PLUGIN_SRC_DIR,
    expected_tag: str | None = None,
) -> dict[str, str]:
    """Validate source layout, metadata keys, icon, license, and optional release tag."""
    if not plugin_dir.is_dir():
        raise FileNotFoundError(f"Plugin source directory not found: {plugin_dir}")

    meta = read_plugin_metadata(plugin_dir)
    missing_keys = [key for key in REQUIRED_METADATA_KEYS if not meta.get(key)]
    if missing_keys:
        raise ValueError(
            "metadata.txt is missing required non-empty keys: " + ", ".join(missing_keys)
        )

    if expected_tag:
        normalized_tag = expected_tag.strip()
        if normalized_tag.startswith("v"):
            normalized_tag = normalized_tag[1:]
        if meta["version"] != normalized_tag:
            raise ValueError(
                f"Tag version '{expected_tag}' (normalized '{normalized_tag}') does not match "
                f"metadata.txt version '{meta['version']}'."
            )

    init_file = plugin_dir / "__init__.py"
    if not init_file.is_file():
        raise FileNotFoundError(f"Missing plugin entrypoint: {init_file}")
    init_text = init_file.read_text(encoding="utf-8")
    if "def classFactory" not in init_text:
        raise ValueError(f"Missing classFactory entrypoint definition in {init_file}")

    license_file = plugin_dir / "LICENSE"
    if not license_file.is_file():
        raise FileNotFoundError(f"Missing LICENSE inside plugin package: {license_file}")

    icon_rel = meta["icon"]
    icon_file = plugin_dir / icon_rel
    if not icon_file.is_file():
        raise FileNotFoundError(f"Missing plugin icon referenced by metadata.txt: {icon_file}")

    return meta


def build_plugin_archive(
    plugin_dir: Path = PLUGIN_SRC_DIR,
    output_dir: Path = DEFAULT_DIST_DIR,
) -> tuple[Path, Path]:
    """Create dist/vector_data_office_boy.zip and dist/SHA256SUMS.txt deterministically."""
    if not plugin_dir.is_dir():
        raise FileNotFoundError(f"Plugin source directory not found: {plugin_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / ARCHIVE_NAME
    checksum_path = output_dir / CHECKSUM_FILENAME

    collected_files: list[tuple[Path, str]] = []
    for root, dirs, files in os.walk(plugin_dir):
        root_path = Path(root)
        rel_root = root_path.relative_to(plugin_dir)
        dirs[:] = sorted(d for d in dirs if d not in FORBIDDEN_DIR_NAMES)
        for filename in sorted(files):
            rel_file = rel_root / filename if str(rel_root) != "." else Path(filename)
            if _is_forbidden_path(rel_file):
                continue
            arcname = f"{PLUGIN_DIR_NAME}/{rel_file.as_posix()}"
            collected_files.append((root_path / filename, arcname))

    if not collected_files:
        raise RuntimeError(f"No distributable files collected from {plugin_dir}")

    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src_file, arcname in collected_files:
            zf.write(src_file, arcname=arcname)

    sha256_hex = _compute_sha256(archive_path)
    checksum_path.write_text(f"{sha256_hex}  {ARCHIVE_NAME}\n", encoding="utf-8")
    return archive_path, checksum_path


def validate_plugin_archive(archive_path: Path) -> None:
    """Inspect the built ZIP archive to verify QGIS Plugin Repository compliance."""
    if not archive_path.is_file():
        raise FileNotFoundError(f"Plugin archive does not exist: {archive_path}")

    size_bytes = archive_path.stat().st_size
    if size_bytes <= 0:
        raise ValueError(f"Plugin archive is empty: {archive_path}")
    if size_bytes >= MAX_ARCHIVE_BYTES:
        raise ValueError(
            f"Plugin archive exceeds 25 MB limit ({size_bytes} bytes >= {MAX_ARCHIVE_BYTES} bytes)."
        )

    with zipfile.ZipFile(archive_path, mode="r") as zf:
        names = [info.filename for info in zf.infolist() if not info.is_dir()]

    if not names:
        raise ValueError(f"Plugin archive contains zero file entries: {archive_path}")

    top_levels = {name.split("/", 1)[0] for name in names}
    if top_levels != {PLUGIN_DIR_NAME}:
        raise ValueError(
            f"Plugin archive must contain a single root directory '{PLUGIN_DIR_NAME}/', "
            f"found: {sorted(top_levels)}"
        )

    for name in names:
        parts = Path(name).parts
        if ".." in parts or name.startswith("/"):
            raise ValueError(f"Unsafe archive entry path detected: {name}")
        rel_inside_plugin = Path(*parts[1:])
        if _is_forbidden_path(rel_inside_plugin):
            raise ValueError(f"Forbidden file packaged inside plugin archive: {name}")

    expected_entries = {
        f"{PLUGIN_DIR_NAME}/metadata.txt",
        f"{PLUGIN_DIR_NAME}/__init__.py",
        f"{PLUGIN_DIR_NAME}/LICENSE",
    }
    missing_entries = sorted(expected_entries.difference(names))
    if missing_entries:
        raise ValueError(
            "Plugin archive is missing required entries: " + ", ".join(missing_entries)
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for packaging and validating the QGIS plugin archive."""
    parser = argparse.ArgumentParser(
        description="Build and validate the Vector Data Office Boy QGIS plugin ZIP package."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Destination directory for vector_data_office_boy.zip and SHA256SUMS.txt.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate plugin metadata, entrypoints, icon, license, and ZIP structure.",
    )
    parser.add_argument(
        "--check-tag",
        default=None,
        help="Optional release tag (e.g. v1.0.0) that must match metadata.txt version.",
    )
    args = parser.parse_args(argv)

    try:
        meta = validate_plugin_source(PLUGIN_SRC_DIR, expected_tag=args.check_tag)
        archive_path, checksum_path = build_plugin_archive(
            plugin_dir=PLUGIN_SRC_DIR,
            output_dir=args.output_dir,
        )
        if args.validate or args.check_tag:
            validate_plugin_archive(archive_path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    size_kb = archive_path.stat().st_size / 1024.0
    print(f"Packaged plugin version {meta['version']} -> {archive_path} ({size_kb:.1f} KB)")
    print(f"Wrote SHA-256 checksum -> {checksum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
