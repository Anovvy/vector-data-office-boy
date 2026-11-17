"""Automated weekly dependency, security, and QGIS compatibility maintainer agent."""

from __future__ import annotations

import argparse
import asyncio
import configparser
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pydantic
import requests

REQUEST_TIMEOUT_SECONDS = 20
MODEL_ATTEMPT_TIMEOUT_SECONDS = 25.0
CANDIDATE_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
)
QGIS_RELEASES_LATEST_URL = "https://api.github.com/repos/qgis/QGIS/releases/latest"
QGIS_TAGS_URL = "https://api.github.com/repos/qgis/QGIS/tags"


class MaintenanceReport(pydantic.BaseModel):
    """Structured maintenance recommendation returned by the Antigravity agent."""

    needs_action: bool
    summary: str
    recommended_patches: list[str]
    target_files: list[str]


def parse_metadata_file(metadata_path: Path) -> dict[str, str]:
    """Read [general] metadata values from vector_data_office_boy/metadata.txt."""
    if not metadata_path.is_file():
        return {}
    parser = configparser.RawConfigParser()
    parser.optionxform = str
    parser.read(metadata_path, encoding="utf-8")
    if not parser.has_section("general"):
        return {}
    return {key: val.strip() for key, val in parser.items("general")}


def parse_pip_audit_json(audit_path: Path | None) -> dict[str, Any]:
    """Summarize dependency vulnerabilities from pip-audit JSON output."""
    if audit_path is None or not audit_path.is_file():
        return {"status": "missing", "vulnerable_packages": [], "total_vulns": 0}

    raw_text = audit_path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw_text:
        return {"status": "empty", "vulnerable_packages": [], "total_vulns": 0}

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return {
            "status": f"parse_error: {exc}",
            "vulnerable_packages": [],
            "total_vulns": 0,
            "raw_excerpt": raw_text[:1000],
        }

    if isinstance(payload, dict):
        deps = payload.get("dependencies", [])
    elif isinstance(payload, list):
        deps = payload
    else:
        deps = []

    vulnerable_packages: list[dict[str, Any]] = []
    total_vulns = 0
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        vulns = dep.get("vulns") or []
        if vulns:
            total_vulns += len(vulns)
            vulnerable_packages.append(
                {
                    "name": dep.get("name", "unknown"),
                    "version": dep.get("version", "unknown"),
                    "vulns": [
                        {
                            "id": v.get("id", ""),
                            "fix_versions": v.get("fix_versions", []),
                            "aliases": v.get("aliases", []),
                        }
                        for v in vulns
                        if isinstance(v, dict)
                    ],
                }
            )

    return {
        "status": "ok",
        "dependency_count": len(deps),
        "total_vulns": total_vulns,
        "vulnerable_packages": vulnerable_packages,
    }


def _normalize_qgis_tag_version(tag_name: str) -> str:
    """Extract dotted X.Y.Z version string from a QGIS GitHub tag (e.g., final-3_44_15)."""
    match = re.search(r"(\d+)[._](\d+)(?:[._](\d+))?", tag_name)
    if not match:
        return tag_name.strip()
    major, minor, patch = match.group(1), match.group(2), match.group(3)
    return f"{major}.{minor}.{patch}" if patch is not None else f"{major}.{minor}"


def fetch_latest_qgis_release(github_token: str | None = None) -> dict[str, str]:
    """Fetch the latest QGIS release tag/version via the GitHub REST API."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vector-data-office-boy-auto-maintainer",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        resp = requests.get(
            QGIS_RELEASES_LATEST_URL,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if resp.status_code == 200:
            data = resp.json()
            tag = str(data.get("tag_name") or "").strip()
            if tag:
                return {
                    "source": "releases/latest",
                    "tag": tag,
                    "version": _normalize_qgis_tag_version(tag),
                    "published_at": str(data.get("published_at") or ""),
                }
    except Exception as exc:
        print(f"Warning: QGIS releases/latest query failed ({exc}); falling back to tags.")

    try:
        resp = requests.get(
            QGIS_TAGS_URL,
            headers=headers,
            params={"per_page": 15},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        tags = resp.json()
        if isinstance(tags, list):
            for entry in tags:
                if not isinstance(entry, dict):
                    continue
                tag = str(entry.get("name") or "").strip()
                if tag.startswith("final-"):
                    return {
                        "source": "tags",
                        "tag": tag,
                        "version": _normalize_qgis_tag_version(tag),
                        "published_at": "",
                    }
            if tags and isinstance(tags[0], dict):
                tag = str(tags[0].get("name") or "").strip()
                return {
                    "source": "tags",
                    "tag": tag,
                    "version": _normalize_qgis_tag_version(tag),
                    "published_at": "",
                }
    except Exception as exc:
        return {
            "source": "unavailable",
            "tag": "unknown",
            "version": "unknown",
            "error": str(exc),
        }

    return {"source": "unavailable", "tag": "unknown", "version": "unknown"}


def _read_text_truncated(path: Path | None, max_chars: int) -> str:
    if path is None or not path.is_file():
        return "(Not provided)"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [TRUNCATED] ..."


def _build_system_instruction(agent_md_text: str) -> str:
    return (
        "You are an automated Senior Release & Security Maintenance Engineer for the "
        "'Vector Data Office Boy' QGIS 3.x plugin repository.\n\n"
        "Audit the weekly pip-audit vulnerability output, current plugin metadata.txt "
        "(qgisMinimumVersion and version), Pytest verification log, and latest upstream "
        "QGIS release version.\n"
        "Rules:\n"
        "1. Write 100% English output with strictly zero emojis.\n"
        "2. Set needs_action=true ONLY when there are actionable security vulnerabilities "
        "with available fixes, failing tests, or urgent QGIS compatibility changes needed.\n"
        "3. Keep recommended_patches concrete and scoped to exact target_files "
        "(e.g., requirements-dev.txt, vector_data_office_boy/metadata.txt, .github/workflows/ci.yml).\n"
        "4. Never recommend breaking QGIS 3.22 minimum version support unless critically required.\n\n"
        "--- AGENT.MD SUMMARY ---\n"
        f"{agent_md_text}"
    )


def _build_user_prompt(
    metadata: dict[str, str],
    qgis_release: dict[str, str],
    pip_audit_summary: dict[str, Any],
    requirements_text: str,
    pytest_log: str,
) -> str:
    return (
        "Evaluate the weekly maintenance status for Vector Data Office Boy.\n\n"
        f"Plugin metadata.txt:\n"
        f"- name: {metadata.get('name', 'unknown')}\n"
        f"- version: {metadata.get('version', 'unknown')}\n"
        f"- qgisMinimumVersion: {metadata.get('qgisMinimumVersion', 'unknown')}\n\n"
        f"Latest Upstream QGIS Release:\n{json.dumps(qgis_release, indent=2)}\n\n"
        f"pip-audit Vulnerability Summary:\n{json.dumps(pip_audit_summary, indent=2)}\n\n"
        f"requirements-dev.txt:\n```text\n{requirements_text}\n```\n\n"
        f"Pytest Verification Output:\n```text\n{pytest_log}\n```"
    )


async def _run_antigravity_maintainer(
    api_key: str,
    system_instruction: str,
    user_prompt: str,
) -> tuple[MaintenanceReport, str]:
    from google.antigravity import Agent, LocalAgentConfig, types
    from google.antigravity.hooks import policy

    last_error: Exception | None = None
    for model_name in CANDIDATE_MODELS:
        config = LocalAgentConfig(
            api_key=api_key,
            model=model_name,
            system_instructions=types.TemplatedSystemInstructions(identity=system_instruction),
            response_schema=MaintenanceReport,
            retry_config=types.RetryConfig(
                api_retry=types.ModelAPIRetryConfig(
                    max_retries=0,
                )
            ),
            budget_config=types.BudgetConfig(
                max_model_calls=1,
                max_tool_calls=0,
                max_total_tokens=120_000,
            ),
            capabilities=types.CapabilitiesConfig(
                agent_behavior=types.AgentBehavior.AUTONOMOUS,
            ),
            policies=[policy.deny_all()],
        )
        try:
            async with Agent(config=config) as agent:
                response = await asyncio.wait_for(
                    agent.chat(user_prompt),
                    timeout=MODEL_ATTEMPT_TIMEOUT_SECONDS,
                )
                data = await response.structured_output()
                if not data:
                    raw_text = await response.text()
                    if not raw_text or not raw_text.strip():
                        raise RuntimeError(
                            f"Model {model_name} returned empty output (possible 429/503)."
                        )
                    data = json.loads(raw_text)
                return (
                    MaintenanceReport.model_validate(data),
                    f"Google Antigravity SDK ({model_name})",
                )
        except Exception as exc:
            last_error = exc
            print(f"Antigravity model {model_name} failed ({exc}); trying next candidate...")

    raise RuntimeError(f"All Antigravity candidate models failed: {last_error}")


def _run_genai_fallback_maintainer(
    api_key: str,
    system_instruction: str,
    user_prompt: str,
) -> tuple[MaintenanceReport, str]:
    from google import genai

    client = genai.Client(api_key=api_key)
    last_error: Exception | None = None
    for model_name in CANDIDATE_MODELS:
        try:
            interaction = client.interactions.create(
                model=model_name,
                input=user_prompt,
                system_instruction=system_instruction,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": MaintenanceReport.model_json_schema(),
                },
            )
            if not interaction.output_text:
                raise RuntimeError(f"Interactions API returned empty output for {model_name}.")
            return (
                MaintenanceReport.model_validate_json(interaction.output_text),
                f"Gemini Interactions API ({model_name} fallback)",
            )
        except Exception as exc:
            last_error = exc
            print(f"Interactions fallback {model_name} failed ({exc}); trying next candidate...")

    raise RuntimeError(f"All Interactions fallback candidate models failed: {last_error}")


def format_markdown_report(
    report: MaintenanceReport,
    engine_label: str,
    metadata: dict[str, str],
    qgis_release: dict[str, str],
    pip_audit_summary: dict[str, Any],
) -> str:
    """Format a clean ASCII-only Markdown report for automated maintenance PRs."""
    action_label = "ACTION REQUIRED" if report.needs_action else "HEALTHY (NO ACTION NEEDED)"
    lines = [
        "## Weekly Automated Maintenance Report",
        "",
        f"- **Status:** `{action_label}`",
        f"- **Analysis Engine:** `{engine_label}`",
        f"- **Plugin Version:** `{metadata.get('version', 'unknown')}` "
        f"(qgisMinimumVersion `{metadata.get('qgisMinimumVersion', 'unknown')}`)",
        f"- **Latest Upstream QGIS Release:** `{qgis_release.get('version', 'unknown')}` "
        f"(tag `{qgis_release.get('tag', 'unknown')}`)",
        f"- **pip-audit Vulnerabilities Found:** `{pip_audit_summary.get('total_vulns', 0)}`",
        "",
        "### Summary",
        report.summary,
        "",
    ]

    if report.target_files:
        lines.append("### Target Files")
        for path_str in report.target_files:
            lines.append(f"- `{path_str}`")
        lines.append("")

    if report.recommended_patches:
        lines.append("### Recommended Patches")
        for idx, patch_item in enumerate(report.recommended_patches, start=1):
            lines.append(f"{idx}. {patch_item}")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for weekly dependency and QGIS compatibility auditing."""
    parser = argparse.ArgumentParser(
        description="Run Antigravity SDK automated weekly maintenance audit."
    )
    parser.add_argument(
        "--pip-audit-json",
        type=Path,
        default=None,
        help="Path to JSON output from `pip-audit --format json`.",
    )
    parser.add_argument(
        "--metadata-txt",
        type=Path,
        default=Path("vector_data_office_boy/metadata.txt"),
        help="Path to plugin metadata.txt.",
    )
    parser.add_argument(
        "--requirements-file",
        type=Path,
        default=Path("requirements-dev.txt"),
        help="Path to requirements-dev.txt.",
    )
    parser.add_argument(
        "--pytest-log",
        type=Path,
        default=None,
        help="Optional Pytest verification output log.",
    )
    parser.add_argument(
        "--agent-md",
        type=Path,
        default=Path("AGENT.md"),
        help="Path to AGENT.md governance charter.",
    )
    parser.add_argument(
        "--qgis-latest-tag",
        default=None,
        help="Optional pre-fetched upstream QGIS release tag (skips GitHub API query).",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to write structured JSON report.",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=None,
        help="Optional path to write Markdown summary report.",
    )
    args = parser.parse_args(argv)

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        return 1

    metadata = parse_metadata_file(args.metadata_txt)
    pip_audit_summary = parse_pip_audit_json(args.pip_audit_json)
    if args.qgis_latest_tag:
        qgis_release = {
            "source": "cli_override",
            "tag": args.qgis_latest_tag.strip(),
            "version": _normalize_qgis_tag_version(args.qgis_latest_tag),
        }
    else:
        github_token = os.environ.get("GITHUB_TOKEN", "").strip() or None
        qgis_release = fetch_latest_qgis_release(github_token=github_token)

    agent_md_text = _read_text_truncated(args.agent_md, 10000)
    requirements_text = _read_text_truncated(args.requirements_file, 4000)
    pytest_log = _read_text_truncated(args.pytest_log, 4000)

    system_instruction = _build_system_instruction(agent_md_text)
    user_prompt = _build_user_prompt(
        metadata=metadata,
        qgis_release=qgis_release,
        pip_audit_summary=pip_audit_summary,
        requirements_text=requirements_text,
        pytest_log=pytest_log,
    )

    try:
        report, engine_label = asyncio.run(
            _run_antigravity_maintainer(api_key, system_instruction, user_prompt)
        )
    except Exception as agy_exc:
        print(f"Antigravity SDK runtime unavailable ({agy_exc}); using google-genai fallback.")
        report, engine_label = _run_genai_fallback_maintainer(
            api_key, system_instruction, user_prompt
        )

    markdown_report = format_markdown_report(
        report=report,
        engine_label=engine_label,
        metadata=metadata,
        qgis_release=qgis_release,
        pip_audit_summary=pip_audit_summary,
    )
    json_payload = {
        "needs_action": report.needs_action,
        "summary": report.summary,
        "recommended_patches": report.recommended_patches,
        "target_files": report.target_files,
        "engine": engine_label,
        "qgis_release": qgis_release,
        "pip_audit_total_vulns": pip_audit_summary.get("total_vulns", 0),
    }

    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(json_payload, indent=2) + "\n", encoding="utf-8")

    if args.output_md is not None:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(markdown_report, encoding="utf-8")

    print(json.dumps(json_payload, indent=2))
    print("\n" + markdown_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
