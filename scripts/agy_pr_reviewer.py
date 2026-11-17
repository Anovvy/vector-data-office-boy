"""Automated Senior QA & GIS Engineer Pull Request Reviewer using Google Antigravity SDK."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Literal

import pydantic
import requests

MAX_DIFF_CHARS = 45000
MAX_TEST_LOG_CHARS = 6000
REQUEST_TIMEOUT_SECONDS = 30


class QAReviewReport(pydantic.BaseModel):
    """Structured QA review output returned by the review agent."""

    verdict: Literal["APPROVE", "REQUEST_CHANGES"]
    summary: str
    agent_md_compliance: bool
    critical_issues: list[str]
    warnings: list[str]
    recommended_actions: list[str]


def _read_truncated(path: Path | None, limit: int) -> str:
    if path is None or not path.exists():
        return "(Not provided)"
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) <= limit:
        return content
    return content[:limit] + "\n... [TRUNCATED DUE TO LENGTH] ..."


def _build_system_instruction(agent_md_text: str) -> str:
    return (
        "You are a Senior QA and GIS Software Engineer reviewing a Pull Request "
        "for the QGIS plugin repository 'Vector Data Office Boy'.\n\n"
        "You must audit the Pull Request strictly against the repository governance "
        "charter (AGENT.md) and GIS software engineering best practices:\n"
        "1. Language & Zero-Emoji Policy: Verify all files in the repository are 100% "
        "English and contain zero emojis.\n"
        "2. Scope & Role Charter: Check that tests do not mutate data_test/vector_data_test.shp, "
        "Qt Designer .ui files use standard Qt5 widgets only, and core fixes preserve outer dataset "
        "boundaries during gap filling.\n"
        "3. Code Quality & Safety: Check for bare except clauses, unsafe file overwrites, missing "
        "CRS handling, or invalid/degenerate geometry assumptions (None, empty, GeometryCollection).\n"
        "4. Verdict Rule: Set verdict to 'REQUEST_CHANGES' if any critical policy violation, test "
        "failure, security issue, or geometry edge-case bug is found. Otherwise set 'APPROVE'.\n\n"
        "--- AGENT.MD CHARTER ---\n"
        f"{agent_md_text}"
    )


def _build_user_prompt(
    pr_title: str,
    pr_body: str,
    changed_files: str,
    diff_text: str,
    test_log: str,
) -> str:
    return (
        f"Review this Pull Request targeting 'main'.\n\n"
        f"PR Title: {pr_title}\n"
        f"PR Description:\n{pr_body or '(Empty)'}\n\n"
        f"Changed Files:\n{changed_files}\n\n"
        f"Pytest / CI Execution Summary:\n{test_log}\n\n"
        f"Unified Git Diff:\n```diff\n{diff_text}\n```"
    )


CANDIDATE_MODELS = (
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
)


async def _run_antigravity_review(
    api_key: str,
    system_instruction: str,
    user_prompt: str,
) -> tuple[QAReviewReport, str]:
    from google.antigravity import Agent, LocalAgentConfig, types
    from google.antigravity.hooks import policy

    last_error: Exception | None = None
    for model_name in CANDIDATE_MODELS:
        config = LocalAgentConfig(
            api_key=api_key,
            model=model_name,
            system_instructions=types.TemplatedSystemInstructions(identity=system_instruction),
            response_schema=QAReviewReport,
            retry_config=types.RetryConfig(
                api_retry=types.ModelAPIRetryConfig(
                    max_retries=4,
                    initial_sleep_duration_ms=2000,
                    exponential_multiplier=2.0,
                    jitter_range=0.2,
                )
            ),
            budget_config=types.BudgetConfig(
                max_model_calls=4,
                max_tool_calls=8,
                max_total_tokens=120_000,
            ),
            capabilities=types.CapabilitiesConfig(
                agent_behavior=types.AgentBehavior.AUTONOMOUS,
            ),
            policies=[
                policy.allow("view_file"),
                policy.allow("list_directory"),
                policy.allow("search_directory"),
                policy.allow("find_file"),
                policy.deny_all(),
            ],
        )
        try:
            async with Agent(config=config) as agent:
                response = await agent.chat(user_prompt)
                data = await response.structured_output()
                if not data:
                    raw_text = await response.text()
                    if not raw_text or not raw_text.strip():
                        raise RuntimeError(
                            f"Model {model_name} returned empty output (possible transient 503)."
                        )
                    data = json.loads(raw_text)
                return (
                    QAReviewReport.model_validate(data),
                    f"Google Antigravity SDK ({model_name})",
                )
        except Exception as exc:
            last_error = exc
            print(f"Antigravity model {model_name} failed ({exc}); trying next candidate...")

    raise RuntimeError(f"All Antigravity candidate models failed: {last_error}")


def _run_genai_fallback_review(
    api_key: str,
    system_instruction: str,
    user_prompt: str,
) -> tuple[QAReviewReport, str]:
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
                    "schema": QAReviewReport.model_json_schema(),
                },
            )
            if not interaction.output_text:
                raise RuntimeError(f"Interactions API returned empty output for {model_name}.")
            return (
                QAReviewReport.model_validate_json(interaction.output_text),
                f"Gemini Interactions API ({model_name} fallback)",
            )
        except Exception as exc:
            last_error = exc
            print(f"Interactions fallback {model_name} failed ({exc}); trying next candidate...")

    raise RuntimeError(f"All Interactions fallback candidate models failed: {last_error}")


def _format_comment_markdown(
    report: QAReviewReport,
    engine_label: str,
    commit_sha: str,
) -> str:
    status_label = (
        "APPROVED" if report.verdict == "APPROVE" else "REQUEST CHANGES (ACTION REQUIRED)"
    )
    compliance_label = "PASS" if report.agent_md_compliance else "FAIL"

    lines = [
        "<!-- agy-pr-qa-reviewer -->",
        f"## Senior QA Engineer Review ({status_label})",
        "",
        f"- **Review Engine:** `{engine_label}`",
        f"- **Reviewed Commit:** `{commit_sha[:7]}`",
        f"- **AGENT.md Compliance:** `{compliance_label}`",
        "",
        "### Summary",
        report.summary,
        "",
    ]

    if report.critical_issues:
        lines.append("### Critical Issues (Must Fix Before Merge)")
        for issue in report.critical_issues:
            lines.append(f"- {issue}")
        lines.append("")

    if report.warnings:
        lines.append("### Warnings & Edge-Case Notes")
        for warn in report.warnings:
            lines.append(f"- {warn}")
        lines.append("")

    if report.recommended_actions:
        lines.append("### Recommended Actions")
        for idx, action in enumerate(report.recommended_actions, start=1):
            lines.append(f"{idx}. {action}")
        lines.append("")

    return "\n".join(lines)


def _post_pr_comment(
    repo: str,
    pr_number: int,
    github_token: str,
    markdown_body: str,
) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = requests.post(
        url,
        headers=headers,
        json={"body": markdown_body},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()


def main() -> int:
    """Run automated PR review and post feedback to the GitHub Pull Request."""
    parser = argparse.ArgumentParser(
        description="Run Antigravity SDK QA review on a GitHub Pull Request."
    )
    parser.add_argument("--repo", required=True, help="GitHub repository (owner/name).")
    parser.add_argument("--pr-number", required=True, type=int, help="Pull Request number.")
    parser.add_argument("--commit-sha", default="unknown", help="Head commit SHA of the PR.")
    parser.add_argument("--pr-title", default="", help="Pull Request title.")
    parser.add_argument("--pr-body-file", type=Path, default=None, help="Path to PR body text.")
    parser.add_argument("--changed-files", type=Path, required=True, help="Changed files list.")
    parser.add_argument("--diff-file", type=Path, required=True, help="Unified diff file.")
    parser.add_argument("--test-log", type=Path, default=None, help="Pytest log file.")
    parser.add_argument(
        "--agent-md",
        type=Path,
        default=Path("AGENT.md"),
        help="Path to AGENT.md governance charter.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        return 1
    if not github_token:
        print("ERROR: GITHUB_TOKEN environment variable is not set.", file=sys.stderr)
        return 1

    agent_md_text = _read_truncated(args.agent_md, 15000)
    pr_body = _read_truncated(args.pr_body_file, 4000)
    changed_files = _read_truncated(args.changed_files, 4000)
    diff_text = _read_truncated(args.diff_file, MAX_DIFF_CHARS)
    test_log = _read_truncated(args.test_log, MAX_TEST_LOG_CHARS)

    system_instruction = _build_system_instruction(agent_md_text)
    user_prompt = _build_user_prompt(
        pr_title=args.pr_title,
        pr_body=pr_body,
        changed_files=changed_files,
        diff_text=diff_text,
        test_log=test_log,
    )

    try:
        report, engine_label = asyncio.run(
            _run_antigravity_review(api_key, system_instruction, user_prompt)
        )
    except Exception as agy_exc:
        print(f"Antigravity SDK runtime unavailable ({agy_exc}); using google-genai fallback.")
        report, engine_label = _run_genai_fallback_review(
            api_key, system_instruction, user_prompt
        )

    comment_md = _format_comment_markdown(report, engine_label, args.commit_sha)
    _post_pr_comment(args.repo, args.pr_number, github_token, comment_md)
    print(f"Posted PR QA review comment with verdict: {report.verdict}")

    if report.verdict == "REQUEST_CHANGES":
        print("QA Reviewer requested changes before merge.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
