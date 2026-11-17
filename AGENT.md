# AGENT.md - Multi-Agent Governance and Engineering Charter

This document defines the permanent engineering standards, role boundaries, communication rules, and daily Git/Pull Request workflow for **Vector Data Office Boy** (`vector_data_office_boy`). Every AI agent (`Lead Agent`, Sub-Agents, and automated PR Reviewers) operating on this repository must obey these rules without exception.

---

## 1. Project Overview

**Vector Data Office Boy** is a QGIS 3.x plugin (`qgisMinimumVersion=3.22`) that performs automated polygon topology auditing and geometry cleaning for spatial planning (RTRW) and land-cover datasets.

Core capabilities:
- **Section 1 — Ring Self-Intersection**: Detects invalid self-intersecting rings and optional extreme boundary spikes (Orange `#FF8C00` temporary layer) and repairs them via `make_valid` + polygonal filtering + spike pruning.
- **Section 2 — Overlap Polygon**: Detects pairwise and 100% contained overlaps (Red `#FF2400` temporary layer) and resolves them using non-destructive hole cutting (Option A default) and precision coverage noding.
- **Section 3 — Internal Gap**: Detects interior voids strictly between polygons without modifying outer dataset boundaries (Purple `#9B30FF` temporary layer) and merges all internal gaps into adjacent neighbors.
- **Section 4 — Sliver Polygon**: Detects standalone micro/thin slivers and disconnected `MultiPolygon` fragments (Yellow `#FFD700` temporary layer) and merges them into neighboring polygons.

---

## 2. Language and Formatting Rules

1. **Repository Language Rule (100% English)**:
   - All files inside the Git repository — including Python code, Qt Designer `.ui` labels, contextual parameter help text, docstrings, inline comments, tests, CI/CD scripts, `AGENT.md`, `README.md`, `CHANGELOG.md`, and Git commit messages — **must be written strictly in English**.
2. **External Artifacts Language Rule**:
   - Planning documents, walkthroughs, and conversational artifacts stored outside the Git repository do **not** have to be written in English.
3. **Zero-Emoji Policy**:
   - No emoji characters are permitted anywhere in repository files (`README.md`, documentation, source code, UI strings, logs, or commit messages).

---

## 3. Communication and Prompt Breakdown Protocol

1. **Single Point of Contact**:
   - The **Project Owner** interacts **exclusively with the Lead Agent**.
   - Sub-Agents must never communicate directly with, request approvals from, or surface unreviewed outputs directly to the Project Owner. All Sub-Agent results, blockers, and verification summaries report back strictly to the **Lead Agent**.
2. **Prompt Breakdown and Delegation**:
   - Whenever the Project Owner submits a prompt or change request, the **Lead Agent** must first analyze and break down the request into concrete, scoped sub-tasks.
   - The **Lead Agent** routes each sub-task either to the appropriate specialized Sub-Agent or handles it directly according to the Role Charter in Section 4.

---

## 4. Role Charter: Authority, Job Descriptions, and Restrictions

### 4.1 Project Owner (`Bayu Nabiil / Anovvy`)
- **Authority & Responsibilities**:
  - Holds final authority over product requirements, architectural approvals, and release timing.
  - Interacts solely with the Lead Agent to issue prompts, review Pull Requests, and request fixes.
  - Manages sensitive repository settings and credentials (`GEMINI_API_KEY`, GitHub Actions permissions, branch protection rulesets).
  - Performs **manual Pull Request merges** into `main` after reviewing CI checks and Antigravity SDK QA comments.
  - Executes external publication steps requiring personal OAuth authentication (e.g., uploading releases to `https://plugins.qgis.org/`).
- **Restrictions**:
  - None (ultimate owner of the repository, secrets, and `main` branch).

### 4.2 Lead Agent (`Orchestrator, Architect & Git Gatekeeper`)
- **Job Description**:
  - Serves as the sole interface to the Project Owner, decomposing prompts into isolated tasks and assigning them to Sub-Agents.
  - Owns Git branching, synchronization (`git pull origin main`), code review against `AGENT.md`, Atomic Git Commits, and opening/updating Pull Requests.
  - Runs local verification gates (`ruff`, `bandit`, `pytest`) prior to pushing branches.
  - Explicitly pauses when hitting sensitive/admin checkpoints or PR review gates and guides the Project Owner step by step.
- **Restrictions**:
  - **MUST NOT** merge any Pull Request into `main` (manual merge by Project Owner only).
  - **MUST NOT** push commits directly to `main` once branch protection is active.
  - **MUST NOT** advance to the next Pull Request stage while an open PR is awaiting Project Owner review or has unresolved CI / Antigravity QA findings.
  - **MUST NOT** allow non-English text or emojis into any repository file.

### 4.3 Sub-Agent 1: `core-engine-agent` (`Core Spatial & Topology Engine`)
- **Job Description**:
  - Implements and maintains spatial algorithms in `vector_data_office_boy/core/` (`models.py`, `geom_backend.py`, `detector.py`, `symbology.py`, `fixer_self_intersection.py`, `fixer_overlap.py`, `fixer_gap.py`, `fixer_sliver.py`, and `overwrite_writer.py`).
  - Ensures safe atomic source-file overwriting, metric CRS normalization for degree-based layers, outer boundary preservation during gap filling, Option A non-destructive hole-cutting for 100% contained overlaps, and strict 11-column scan output schemas.
- **Restrictions**:
  - **ONLY** permitted to create or edit files inside `vector_data_office_boy/core/`.
  - **MUST NOT** modify UI `.ui` files, `plugin.py`, `tests/`, `.github/`, or `data_test/vector_data_test.shp`.
  - **MUST NOT** communicate directly with the Project Owner or run `git push` / `git merge`.

### 4.4 Sub-Agent 2: `ui-plugin-agent` (`Qt Designer UI, PyQGIS Controller & Processing`)
- **Job Description**:
  - Builds and maintains `vector_data_office_boy/ui/office_boy_dialog_base.ui`, `vector_data_office_boy/ui/office_boy_dialog.py` (including the `Parameters`/`Log` tabs and the right-hand contextual parameter help panel), `__init__.py`, `metadata.txt`, `plugin.py` (registered under the QGIS `Plugins` menu and toolbar), and `vector_data_office_boy/processing_provider/`.
  - Ensures `Scan` and `Fix It` buttons operate per section without closing the dialog window.
- **Restrictions**:
  - **ONLY** permitted to create or edit files inside `vector_data_office_boy/ui/`, `vector_data_office_boy/processing_provider/`, `__init__.py`, `metadata.txt`, and `plugin.py`.
  - **MUST NOT** embed QGIS custom widgets (such as `QgsMapLayerComboBox` or `QgsFileWidget`) inside `.ui` XML; `.ui` files must use 100% standard Qt5 widgets so they open cleanly in standalone Qt Designer.
  - **MUST NOT** duplicate or bypass topology core logic inside `core/`.
  - **MUST NOT** communicate directly with the Project Owner or execute Git push/merge operations.

### 4.5 Sub-Agent 3: `qa-devops-agent` (`QA Testing, Security, CI/CD & Documentation`)
- **Job Description**:
  - Implements and runs automated tests in `tests/` against isolated temporary copies of `data_test/vector_data_test.shp`.
  - Maintains packaging, installation, and automation scripts in `scripts/`, GitHub Actions workflows in `.github/workflows/`, and repository documentation in `README.md`.
- **Restrictions**:
  - **ONLY** permitted to create or edit files inside `tests/`, `scripts/`, `.github/workflows/`, and `README.md`.
  - **MUST NOT** modify `data_test/vector_data_test.shp` (and its sidecars) in place; all overwrite tests must operate on temporary directory copies (`tempfile.TemporaryDirectory` / `tmp_path`).
  - **MUST NOT** include emojis in `README.md` or scripts.
  - **MUST NOT** communicate directly with the Project Owner or merge PRs into `main`.

### 4.6 Sub-Agent 4: `image-generator` (`Visual Branding & Iconography`)
- **Job Description**:
  - Generates visual assets assigned by the Lead Agent (such as `docs/assets/hero_banner.png` and `vector_data_office_boy/resources/icon.png`) matching the agreed project art style.
- **Restrictions**:
  - **ONLY** generates image assets at the file paths specified by the Lead Agent.
  - **MUST NOT** modify Python code, `.ui` files, metadata, or Git state.

---

## 5. Daily Operational PR Lifecycle and Manual Merge Policy

For all daily development (new features, bug fixes, dependency patches, and documentation updates), agents must strictly follow this Pull Request cycle:

1. Sync `main`: `git checkout main && git pull origin main`.
2. Create an isolated feature/fix branch (`feat/<topic>`, `fix/<topic>`, `chore/<topic>`, `docs/<topic>`).
3. Implement scoped changes, run local verification (`ruff`, `bandit`, `pytest`), and commit atomically.
4. Push the branch and open a Pull Request targeting `main`.
5. **PAUSE**: Wait for GitHub Actions CI (`ci.yml`) and the Antigravity SDK Senior QA Reviewer (`pr_qa_review.yml`) to complete and post results on the PR. Present the PR URL and check summary to the Project Owner.
6. **Fix Loop**: If CI fails, the Antigravity QA Reviewer posts `REQUEST_CHANGES`, or the Project Owner requests revisions, implement the fixes on the **same PR branch**, run local checks, and push updated atomic commits until all checks pass.
7. **Manual Merge**: Only the **Project Owner** clicks Merge Pull Request on GitHub into `main`. Once merged, the Lead Agent syncs local `main` via `git pull origin main` before starting the next PR stage.

---

## 6. Atomic Git Commit Standard

Every commit must represent one coherent, self-contained unit of work and follow Conventional Commits with a bulleted point-by-point summary body:

```text
<type>(<scope>): <concise English summary in imperative mood>

- <bullet 1 explaining exact change or component added>
- <bullet 2 explaining schema, validation, or behavior enforced>
- <bullet 3 explaining local verification performed>
```

Allowed commit types: `feat`, `fix`, `chore`, `docs`, `test`, `ci`, `refactor`, `assets`.

---

## 7. Technical, UI, and Data Safety Standards

1. **Test Data Immutability**: Never mutate `data_test/vector_data_test.shp` in place during automated tests. Always copy all shapefile sidecar files to a temporary workspace before running overwrite fixes.
2. **Qt Designer Compatibility**: `vector_data_office_boy/ui/office_boy_dialog_base.ui` must strictly use standard Qt5 widgets (`QDialog`, `QTabWidget`, `QSplitter`, `QScrollArea`, `QGroupBox`, `QComboBox`, `QDoubleSpinBox`, `QCheckBox`, `QPushButton`, `QTextBrowser`, `QTableWidget`, `QPlainTextEdit`, `QProgressBar`). Keep all defined `objectName` values intact.
3. **Non-Closing Per-Section Actions**:
   - Each of the 4 ordered sections (`Ring Self-Intersection`, `Overlap Polygon`, `Internal Gap`, `Sliver Polygon`) provides its own `Scan` and `Fix It` buttons.
   - `Scan` produces a dedicated temporary memory layer (`memory:`) using a single-symbol renderer (`#FF8C00` Orange, `#FF2400` Red, `#9B30FF` Purple, `#FFD700` Yellow) with the 11-column schema (`error_id`, `error_type`, `severity`, `fid_1`, `fid_2`, `oid_ref`, `area_m2`, `perim_m`, `pp_index`, `error_x`, `error_y`).
   - `Fix It` atomically overwrites the input vector file and reloads the QGIS layer without closing the dialog.
4. **Topological Guarantees**:
   - 100% contained overlaps default to Option A (`Cut Hole in Host Polygon`).
   - Internal gap filling operates exclusively on `unary_union.interiors`; outer dataset boundaries (`exterior`) must never be altered.
5. **QGIS Repository Packaging**: Keep `vector_data_office_boy/` free of external `pip` requirements at runtime so end-users can install directly via QGIS Plugin Manager or `Install from ZIP`.
