"""QGIS Processing algorithms for Vector Data Office Boy (4 Scan + 4 Fix)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vector_data_office_boy.processing_provider.core_bridge import (
    GAP_RULE_LABELS,
    OVERLAP_RULE_LABELS,
    SLIVER_RULE_LABELS,
    build_gap_config,
    build_overlap_config,
    build_self_intersection_config,
    build_sliver_config,
    ensure_core_available,
    extract_operation_summary,
    fix_internal_gaps,
    fix_overlaps,
    fix_ring_self_intersections,
    fix_slivers,
    invoke_core_operation,
    register_error_layer,
    scan_internal_gaps,
    scan_overlaps,
    scan_ring_self_intersections,
    scan_slivers,
)

try:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsField,
        QgsFields,
        QgsGeometry,
        QgsProcessing,
        QgsProcessingAlgorithm,
        QgsProcessingException,
        QgsProcessingOutputNumber,
        QgsProcessingOutputVectorLayer,
        QgsProcessingParameterBoolean,
        QgsProcessingParameterEnum,
        QgsProcessingParameterFeatureSink,
        QgsProcessingParameterNumber,
        QgsProcessingParameterVectorLayer,
        QgsProject,
        QgsWkbTypes,
    )
    from qgis.PyQt.QtCore import QVariant

    QGIS_AVAILABLE = True
except ImportError:  # pragma: no cover - allows importing package outside QGIS
    QGIS_AVAILABLE = False
    QgsProcessingAlgorithm = object  # type: ignore[assignment,misc]
    QVariant = None  # type: ignore[assignment,misc]


class _BaseOfficeBoyAlgorithm(QgsProcessingAlgorithm):
    """Base class for all Vector Data Office Boy Processing algorithms."""

    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    OUTPUT_LAYER = "OUTPUT_LAYER"
    ERROR_COUNT = "ERROR_COUNT"
    TOTAL_AREA_M2 = "TOTAL_AREA_M2"
    FIXED_COUNT = "FIXED_COUNT"
    ADD_STYLED_LAYER = "ADD_STYLED_LAYER"
    RECALC_ATTRS = "RECALC_ATTRS"

    _alg_name: str = ""
    _display_name: str = ""
    _group_name: str = ""
    _group_id: str = ""
    _short_help: str = ""
    _section_label: str = ""

    def createInstance(self) -> _BaseOfficeBoyAlgorithm:
        """Create a fresh instance for QGIS Processing registry."""
        return type(self)()

    def name(self) -> str:
        """Return unique machine-readable algorithm ID."""
        return self._alg_name

    def displayName(self) -> str:
        """Return human-readable algorithm title."""
        return self._display_name

    def group(self) -> str:
        """Return Processing Toolbox subgroup title."""
        return self._group_name

    def groupId(self) -> str:
        """Return Processing Toolbox subgroup identifier."""
        return self._group_id

    def shortHelpString(self) -> str:
        """Return English algorithm help text for Processing dialog."""
        return self._short_help

    def flags(self) -> Any:
        """Execute on the main thread when modifying QGIS layers and project state."""
        if not QGIS_AVAILABLE:
            return 0
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def _require_valid_polygon_layer(
        self,
        parameters: dict[str, Any],
        context: Any,
        *,
        for_fix: bool = False,
    ) -> Any:
        """Fetch and validate the input polygon vector layer."""
        if not QGIS_AVAILABLE:
            raise RuntimeError("QGIS Processing environment is not available.")
        ensure_core_available()

        layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if layer is None or not layer.isValid():
            raise QgsProcessingException("Please select a valid polygon vector layer.")
        if layer.geometryType() != QgsWkbTypes.PolygonGeometry:
            raise QgsProcessingException(
                "Input layer must be a polygon geometry layer (Polygon or MultiPolygon)."
            )
        if for_fix and layer.isEditable():
            raise QgsProcessingException(
                "Input layer currently has an active edit session. Save or roll back edits "
                "before running an in-place geometry fix."
            )
        return layer

    def _add_common_input_layer_param(self) -> None:
        """Register the mandatory polygon input layer parameter."""
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT,
                "Input Polygon Layer",
                [QgsProcessing.TypeVectorPolygon],
            )
        )

    def _run_scan(
        self,
        parameters: dict[str, Any],
        context: Any,
        feedback: Any,
        scan_fn: Callable[..., Any],
        config: Any,
    ) -> dict[str, Any]:
        """Execute a core scan function and populate Processing outputs."""
        layer = self._require_valid_polygon_layer(parameters, context, for_fix=False)
        add_styled = self.parameterAsBool(parameters, self.ADD_STYLED_LAYER, context)

        feedback.pushInfo(f"Scanning '{layer.name()}' for {self._section_label}...")
        scan_result = invoke_core_operation(scan_fn, layer, config)
        summary = extract_operation_summary(
            scan_result,
            section=self._section_label,
            action="Scan",
        )

        mem_layer = summary.layer
        if add_styled:
            project = context.project() or QgsProject.instance()
            if project is not None:
                registered = register_error_layer(scan_result, project, None)
                if mem_layer is None and registered is not None:
                    mem_layer = registered

        if mem_layer is not None:
            fields = mem_layer.fields()
            wkb_type = mem_layer.wkbType()
            crs = mem_layer.crs()
        else:
            fields = self._build_scan_fields()
            wkb_type = QgsWkbTypes.MultiPolygon
            crs_wkt = getattr(scan_result, "crs_wkt", "")
            crs = (
                QgsCoordinateReferenceSystem.fromWkt(crs_wkt)
                if crs_wkt
                else layer.crs()
            )

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            wkb_type,
            crs,
        )
        if sink is None:
            raise QgsProcessingException("Unable to create output error feature sink.")

        if mem_layer is not None:
            for feat in mem_layer.getFeatures():
                if feedback.isCanceled():
                    break
                sink.addFeature(QgsFeature(feat))
        elif hasattr(scan_result, "records"):
            for rec in scan_result.records:
                if feedback.isCanceled():
                    break
                feat = QgsFeature(fields)
                feat.setAttributes(
                    [
                        rec.error_id,
                        rec.error_type,
                        rec.severity,
                        rec.fid_1,
                        str(rec.fid_2),
                        str(rec.oid_ref),
                        float(rec.area_m2),
                        float(rec.perim_m),
                        float(rec.pp_index),
                        float(rec.error_x),
                        float(rec.error_y),
                    ]
                )
                geom = QgsGeometry()
                if rec.geometry_wkb:
                    geom.fromWkb(rec.geometry_wkb)
                feat.setGeometry(geom)
                sink.addFeature(feat)

        feedback.pushInfo(
            f"{self._section_label} scan complete: {summary.count} error(s), "
            f"total area {summary.total_area_m2:.4f} m2 ({summary.status})."
        )
        for msg in summary.messages:
            feedback.pushInfo(f"  - {msg}")

        return {
            self.OUTPUT: dest_id,
            self.ERROR_COUNT: summary.count,
            self.TOTAL_AREA_M2: summary.total_area_m2,
        }

    def _run_fix(
        self,
        parameters: dict[str, Any],
        context: Any,
        feedback: Any,
        fix_fn: Callable[..., Any],
        config: Any,
    ) -> dict[str, Any]:
        """Execute a core in-place fix function and reload the QGIS layer."""
        layer = self._require_valid_polygon_layer(parameters, context, for_fix=True)
        recalc_attrs = self.parameterAsBool(parameters, self.RECALC_ATTRS, context)

        feedback.pushInfo(f"Fixing {self._section_label} on '{layer.name()}' in place...")
        fix_result = invoke_core_operation(
            fix_fn,
            layer,
            config,
            recalc_attrs=recalc_attrs,
        )
        summary = extract_operation_summary(
            fix_result,
            section=self._section_label,
            action="Fix It",
        )

        layer.reload()
        layer.triggerRepaint()

        feedback.pushInfo(
            f"{self._section_label} fix complete: {summary.count} item(s) processed "
            f"({summary.status})."
        )
        for msg in summary.messages:
            feedback.pushInfo(f"  - {msg}")

        return {
            self.OUTPUT_LAYER: layer.id(),
            self.FIXED_COUNT: summary.count,
        }

    @staticmethod
    def _build_scan_fields() -> Any:
        """Build the canonical 11-column diagnostic scan attribute schema."""
        fields = QgsFields()
        fields.append(QgsField("error_id", QVariant.Int))
        fields.append(QgsField("error_type", QVariant.String))
        fields.append(QgsField("severity", QVariant.String))
        fields.append(QgsField("fid_1", QVariant.Int))
        fields.append(QgsField("fid_2", QVariant.String))
        fields.append(QgsField("oid_ref", QVariant.String))
        fields.append(QgsField("area_m2", QVariant.Double))
        fields.append(QgsField("perim_m", QVariant.Double))
        fields.append(QgsField("pp_index", QVariant.Double))
        fields.append(QgsField("error_x", QVariant.Double))
        fields.append(QgsField("error_y", QVariant.Double))
        return fields

    def _register_scan_outputs(self, output_label: str) -> None:
        """Register common scan sink, styled-layer toggle, and numeric outputs."""
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_STYLED_LAYER,
                "Add or replace styled temporary error layer in current QGIS project",
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                output_label,
                QgsProcessing.TypeVectorPolygon,
            )
        )
        self.addOutput(QgsProcessingOutputNumber(self.ERROR_COUNT, "Detected Error Count"))
        self.addOutput(QgsProcessingOutputNumber(self.TOTAL_AREA_M2, "Total Error Area (m2)"))

    def _register_fix_outputs(self) -> None:
        """Register attribute recalculation parameter and in-place fix outputs."""
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.RECALC_ATTRS,
                "Recalculate area and perimeter attribute fields after fix",
                defaultValue=True,
            )
        )
        self.addOutput(
            QgsProcessingOutputVectorLayer(
                self.OUTPUT_LAYER,
                "Cleaned Polygon Layer (Overwritten In-Place)",
            )
        )
        self.addOutput(QgsProcessingOutputNumber(self.FIXED_COUNT, "Repaired Item Count"))


class ScanRingSelfIntersectionsAlgorithm(_BaseOfficeBoyAlgorithm):
    """Scan polygon layer for ring self-intersections and optional boundary spikes."""

    PRUNE_SPIKES = "PRUNE_SPIKES"
    SPIKE_ANGLE = "SPIKE_ANGLE"

    _alg_name = "scan_ring_self_intersections"
    _display_name = "1. Scan Ring Self-Intersections"
    _group_name = "Topology Audit (Scan)"
    _group_id = "topology_scan"
    _section_label = "1. Ring Self-Intersection"
    _short_help = (
        "Detects invalid polygon rings (bow-tie self-intersections, twisted shells/holes) "
        "and optionally sharp needle-like boundary spikes below the specified angle threshold. "
        "Outputs an 11-column polygon error layer styled in Orange (#FF8C00) when added to the project."
    )

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        """Define algorithm parameters and outputs."""
        if not QGIS_AVAILABLE:
            return
        self._add_common_input_layer_param()
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PRUNE_SPIKES,
                "Detect and prune extreme boundary spikes",
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SPIKE_ANGLE,
                "Minimum Spike Vertex Angle (degrees)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.05,
                maxValue=45.0,
            )
        )
        self._register_scan_outputs("Self-Intersection Errors (Orange)")

    def processAlgorithm(
        self,
        parameters: dict[str, Any],
        context: Any,
        feedback: Any,
    ) -> dict[str, Any]:
        """Run self-intersection diagnostic scan."""
        prune_spikes = self.parameterAsBool(parameters, self.PRUNE_SPIKES, context)
        spike_angle = self.parameterAsDouble(parameters, self.SPIKE_ANGLE, context)
        cfg = build_self_intersection_config(
            prune_spikes=prune_spikes,
            spike_angle=spike_angle,
        )
        return self._run_scan(parameters, context, feedback, scan_ring_self_intersections, cfg)


class ScanOverlapsAlgorithm(_BaseOfficeBoyAlgorithm):
    """Scan polygon layer for pairwise and contained overlapping geometries."""

    MIN_OVERLAP_AREA = "MIN_OVERLAP_AREA"
    INCLUDE_MICRO_OVERLAPS = "INCLUDE_MICRO_OVERLAPS"

    _alg_name = "scan_overlaps"
    _display_name = "2. Scan Overlap Polygons"
    _group_name = "Topology Audit (Scan)"
    _group_id = "topology_scan"
    _section_label = "2. Overlap Polygon"
    _short_help = (
        "Detects pairwise polygon intersections and 100% contained overlaps across the input layer. "
        "Outputs an 11-column polygon error layer styled in Red (#FF2400) when added to the project."
    )

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        """Define algorithm parameters and outputs."""
        if not QGIS_AVAILABLE:
            return
        self._add_common_input_layer_param()
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MIN_OVERLAP_AREA,
                "Minimum Overlap Area (m2)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0001,
                minValue=0.0,
                maxValue=1000000.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.INCLUDE_MICRO_OVERLAPS,
                "Include micro-overlaps below minimum area threshold",
                defaultValue=False,
            )
        )
        self._register_scan_outputs("Overlap Errors (Red)")

    def processAlgorithm(
        self,
        parameters: dict[str, Any],
        context: Any,
        feedback: Any,
    ) -> dict[str, Any]:
        """Run overlap diagnostic scan."""
        min_area = self.parameterAsDouble(parameters, self.MIN_OVERLAP_AREA, context)
        include_micro = self.parameterAsBool(parameters, self.INCLUDE_MICRO_OVERLAPS, context)
        cfg = build_overlap_config(
            rule_index=0,
            min_overlap_area=min_area,
            include_micro_overlap=include_micro,
        )
        return self._run_scan(parameters, context, feedback, scan_overlaps, cfg)


class ScanInternalGapsAlgorithm(_BaseOfficeBoyAlgorithm):
    """Scan polygon layer for interior voids strictly enclosed between features."""

    _alg_name = "scan_internal_gaps"
    _display_name = "3. Scan Internal Gaps"
    _group_name = "Topology Audit (Scan)"
    _group_id = "topology_scan"
    _section_label = "3. Internal Gap"
    _short_help = (
        "Detects interior gaps enclosed strictly within the union of polygons while preserving "
        "the outer boundary of the dataset. Outputs an 11-column polygon error layer styled in "
        "Purple (#9B30FF) when added to the project."
    )

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        """Define algorithm parameters and outputs."""
        if not QGIS_AVAILABLE:
            return
        self._add_common_input_layer_param()
        self._register_scan_outputs("Internal Gap Errors (Purple)")

    def processAlgorithm(
        self,
        parameters: dict[str, Any],
        context: Any,
        feedback: Any,
    ) -> dict[str, Any]:
        """Run internal gap diagnostic scan."""
        cfg = build_gap_config(rule_index=0)
        return self._run_scan(parameters, context, feedback, scan_internal_gaps, cfg)


class ScanSliversAlgorithm(_BaseOfficeBoyAlgorithm):
    """Scan polygon layer for micro-slivers, narrow polygons, and small MultiPolygon parts."""

    SLIVER_AREA = "SLIVER_AREA"
    SLIVER_PP = "SLIVER_PP"
    SLIVER_MAX_AREA_PP = "SLIVER_MAX_AREA_PP"
    PART_MIN_AREA = "PART_MIN_AREA"

    _alg_name = "scan_slivers"
    _display_name = "4. Scan Sliver Polygons"
    _group_name = "Topology Audit (Scan)"
    _group_id = "topology_scan"
    _section_label = "4. Sliver Polygon"
    _short_help = (
        "Detects standalone sliver polygons using an absolute area threshold, Polsby-Popper "
        "compactness ratio capped by maximum area, and disconnected tiny MultiPolygon parts. "
        "Outputs an 11-column polygon error layer styled in Yellow (#FFD700) when added to the project."
    )

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        """Define algorithm parameters and outputs."""
        if not QGIS_AVAILABLE:
            return
        self._add_common_input_layer_param()
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SLIVER_AREA,
                "Absolute Sliver Area Threshold (m2)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=10.0,
                minValue=0.0,
                maxValue=10000000.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SLIVER_PP,
                "Polsby-Popper Compactness Threshold",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.055,
                minValue=0.0001,
                maxValue=1.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SLIVER_MAX_AREA_PP,
                "Max Area for Compactness Rule (m2)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=150.0,
                minValue=0.0,
                maxValue=10000000.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.PART_MIN_AREA,
                "Minimum MultiPolygon Part Area (m2)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.0,
                maxValue=1000000.0,
            )
        )
        self._register_scan_outputs("Sliver Errors (Yellow)")

    def processAlgorithm(
        self,
        parameters: dict[str, Any],
        context: Any,
        feedback: Any,
    ) -> dict[str, Any]:
        """Run sliver diagnostic scan."""
        sliver_area = self.parameterAsDouble(parameters, self.SLIVER_AREA, context)
        sliver_pp = self.parameterAsDouble(parameters, self.SLIVER_PP, context)
        sliver_max_area_pp = self.parameterAsDouble(parameters, self.SLIVER_MAX_AREA_PP, context)
        part_min_area = self.parameterAsDouble(parameters, self.PART_MIN_AREA, context)
        cfg = build_sliver_config(
            sliver_area=sliver_area,
            sliver_pp=sliver_pp,
            sliver_max_area_pp=sliver_max_area_pp,
            part_min_area=part_min_area,
            rule_index=0,
        )
        return self._run_scan(parameters, context, feedback, scan_slivers, cfg)


class FixRingSelfIntersectionsAlgorithm(_BaseOfficeBoyAlgorithm):
    """Repair polygon ring self-intersections and optionally prune boundary spikes in place."""

    PRUNE_SPIKES = "PRUNE_SPIKES"
    SPIKE_ANGLE = "SPIKE_ANGLE"

    _alg_name = "fix_ring_self_intersections"
    _display_name = "1. Fix Ring Self-Intersections (In-Place)"
    _group_name = "Geometry Cleaning (Fix)"
    _group_id = "topology_fix"
    _section_label = "1. Ring Self-Intersection"
    _short_help = (
        "Repairs self-intersecting rings via make_valid and polygonal extraction, optionally "
        "pruning extreme boundary spikes below the angle threshold, and atomically overwrites "
        "the source vector file in place."
    )

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        """Define algorithm parameters and outputs."""
        if not QGIS_AVAILABLE:
            return
        self._add_common_input_layer_param()
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PRUNE_SPIKES,
                "Prune extreme boundary spikes",
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SPIKE_ANGLE,
                "Minimum Spike Vertex Angle (degrees)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.05,
                maxValue=45.0,
            )
        )
        self._register_fix_outputs()

    def processAlgorithm(
        self,
        parameters: dict[str, Any],
        context: Any,
        feedback: Any,
    ) -> dict[str, Any]:
        """Run in-place self-intersection repair."""
        prune_spikes = self.parameterAsBool(parameters, self.PRUNE_SPIKES, context)
        spike_angle = self.parameterAsDouble(parameters, self.SPIKE_ANGLE, context)
        recalc_attrs = self.parameterAsBool(parameters, self.RECALC_ATTRS, context)
        cfg = build_self_intersection_config(
            prune_spikes=prune_spikes,
            spike_angle=spike_angle,
            recalc_attrs=recalc_attrs,
        )
        return self._run_fix(parameters, context, feedback, fix_ring_self_intersections, cfg)


class FixOverlapsAlgorithm(_BaseOfficeBoyAlgorithm):
    """Resolve polygon overlaps according to the selected rule and overwrite in place."""

    OVERLAP_RULE = "OVERLAP_RULE"
    MIN_OVERLAP_AREA = "MIN_OVERLAP_AREA"
    INCLUDE_MICRO_OVERLAPS = "INCLUDE_MICRO_OVERLAPS"

    _alg_name = "fix_overlaps"
    _display_name = "2. Fix Overlap Polygons (In-Place)"
    _group_name = "Geometry Cleaning (Fix)"
    _group_id = "topology_fix"
    _section_label = "2. Overlap Polygon"
    _short_help = (
        "Resolves pairwise and 100% contained overlaps using the chosen resolution strategy "
        "(defaulting to cutting a hole in the host polygon so enclave features remain intact), "
        "and atomically overwrites the source dataset in place."
    )

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        """Define algorithm parameters and outputs."""
        if not QGIS_AVAILABLE:
            return
        self._add_common_input_layer_param()
        self.addParameter(
            QgsProcessingParameterEnum(
                self.OVERLAP_RULE,
                "Overlap Resolution Rule",
                options=list(OVERLAP_RULE_LABELS),
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MIN_OVERLAP_AREA,
                "Minimum Overlap Area (m2)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0001,
                minValue=0.0,
                maxValue=1000000.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.INCLUDE_MICRO_OVERLAPS,
                "Include micro-overlaps below minimum area threshold",
                defaultValue=False,
            )
        )
        self._register_fix_outputs()

    def processAlgorithm(
        self,
        parameters: dict[str, Any],
        context: Any,
        feedback: Any,
    ) -> dict[str, Any]:
        """Run in-place overlap resolution."""
        rule_idx = self.parameterAsEnum(parameters, self.OVERLAP_RULE, context)
        min_area = self.parameterAsDouble(parameters, self.MIN_OVERLAP_AREA, context)
        include_micro = self.parameterAsBool(parameters, self.INCLUDE_MICRO_OVERLAPS, context)
        recalc_attrs = self.parameterAsBool(parameters, self.RECALC_ATTRS, context)
        cfg = build_overlap_config(
            rule_index=rule_idx,
            min_overlap_area=min_area,
            include_micro_overlap=include_micro,
            recalc_attrs=recalc_attrs,
        )
        return self._run_fix(parameters, context, feedback, fix_overlaps, cfg)


class FixInternalGapsAlgorithm(_BaseOfficeBoyAlgorithm):
    """Fill all internal gaps between polygons while preserving outer dataset boundaries."""

    GAP_RULE = "GAP_RULE"

    _alg_name = "fix_internal_gaps"
    _display_name = "3. Fix Internal Gaps (In-Place)"
    _group_name = "Geometry Cleaning (Fix)"
    _group_id = "topology_fix"
    _section_label = "3. Internal Gap"
    _short_help = (
        "Merges all interior voids strictly enclosed between polygons into adjacent neighbors "
        "according to the chosen neighbor rule without altering any outer dataset boundary, and "
        "atomically overwrites the source dataset in place."
    )

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        """Define algorithm parameters and outputs."""
        if not QGIS_AVAILABLE:
            return
        self._add_common_input_layer_param()
        self.addParameter(
            QgsProcessingParameterEnum(
                self.GAP_RULE,
                "Gap Merge Rule",
                options=list(GAP_RULE_LABELS),
                defaultValue=0,
            )
        )
        self._register_fix_outputs()

    def processAlgorithm(
        self,
        parameters: dict[str, Any],
        context: Any,
        feedback: Any,
    ) -> dict[str, Any]:
        """Run in-place internal gap fill."""
        rule_idx = self.parameterAsEnum(parameters, self.GAP_RULE, context)
        recalc_attrs = self.parameterAsBool(parameters, self.RECALC_ATTRS, context)
        cfg = build_gap_config(rule_index=rule_idx, recalc_attrs=recalc_attrs)
        return self._run_fix(parameters, context, feedback, fix_internal_gaps, cfg)


class FixSliversAlgorithm(_BaseOfficeBoyAlgorithm):
    """Merge sliver polygons and tiny MultiPolygon parts into adjacent neighbors in place."""

    SLIVER_AREA = "SLIVER_AREA"
    SLIVER_PP = "SLIVER_PP"
    SLIVER_MAX_AREA_PP = "SLIVER_MAX_AREA_PP"
    PART_MIN_AREA = "PART_MIN_AREA"
    SLIVER_RULE = "SLIVER_RULE"

    _alg_name = "fix_slivers"
    _display_name = "4. Fix Sliver Polygons (In-Place)"
    _group_name = "Geometry Cleaning (Fix)"
    _group_id = "topology_fix"
    _section_label = "4. Sliver Polygon"
    _short_help = (
        "Removes tiny MultiPolygon fragments and merges standalone sliver polygons into neighboring "
        "polygons according to the selected merge rule, overwriting the source dataset atomically "
        "in place."
    )

    def initAlgorithm(self, config: dict[str, Any] | None = None) -> None:
        """Define algorithm parameters and outputs."""
        if not QGIS_AVAILABLE:
            return
        self._add_common_input_layer_param()
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SLIVER_AREA,
                "Absolute Sliver Area Threshold (m2)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=10.0,
                minValue=0.0,
                maxValue=10000000.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SLIVER_PP,
                "Polsby-Popper Compactness Threshold",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.055,
                minValue=0.0001,
                maxValue=1.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SLIVER_MAX_AREA_PP,
                "Max Area for Compactness Rule (m2)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=150.0,
                minValue=0.0,
                maxValue=10000000.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.PART_MIN_AREA,
                "Minimum MultiPolygon Part Area (m2)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.0,
                maxValue=1000000.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.SLIVER_RULE,
                "Sliver Merge Rule",
                options=list(SLIVER_RULE_LABELS),
                defaultValue=0,
            )
        )
        self._register_fix_outputs()

    def processAlgorithm(
        self,
        parameters: dict[str, Any],
        context: Any,
        feedback: Any,
    ) -> dict[str, Any]:
        """Run in-place sliver removal and merging."""
        sliver_area = self.parameterAsDouble(parameters, self.SLIVER_AREA, context)
        sliver_pp = self.parameterAsDouble(parameters, self.SLIVER_PP, context)
        sliver_max_area_pp = self.parameterAsDouble(parameters, self.SLIVER_MAX_AREA_PP, context)
        part_min_area = self.parameterAsDouble(parameters, self.PART_MIN_AREA, context)
        rule_idx = self.parameterAsEnum(parameters, self.SLIVER_RULE, context)
        recalc_attrs = self.parameterAsBool(parameters, self.RECALC_ATTRS, context)
        cfg = build_sliver_config(
            sliver_area=sliver_area,
            sliver_pp=sliver_pp,
            sliver_max_area_pp=sliver_max_area_pp,
            part_min_area=part_min_area,
            rule_index=rule_idx,
            recalc_attrs=recalc_attrs,
        )
        return self._run_fix(parameters, context, feedback, fix_slivers, cfg)


ALL_ALGORITHMS: tuple[type[_BaseOfficeBoyAlgorithm], ...] = (
    ScanRingSelfIntersectionsAlgorithm,
    ScanOverlapsAlgorithm,
    ScanInternalGapsAlgorithm,
    ScanSliversAlgorithm,
    FixRingSelfIntersectionsAlgorithm,
    FixOverlapsAlgorithm,
    FixInternalGapsAlgorithm,
    FixSliversAlgorithm,
)

__all__ = [
    "ALL_ALGORITHMS",
    "FixInternalGapsAlgorithm",
    "FixOverlapsAlgorithm",
    "FixRingSelfIntersectionsAlgorithm",
    "FixSliversAlgorithm",
    "QGIS_AVAILABLE",
    "ScanInternalGapsAlgorithm",
    "ScanOverlapsAlgorithm",
    "ScanRingSelfIntersectionsAlgorithm",
    "ScanSliversAlgorithm",
]
