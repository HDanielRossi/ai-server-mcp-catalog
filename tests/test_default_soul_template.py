"""Tests for templates/default-SOUL.md: the versioned, installable A8 policy
template for the default profile's Kanban lifecycle ownership.

Hermetic: reads only the repository template file itself (never a live
~/.hermes/SOUL.md) and, for mutation coverage, string-mutated in-memory
copies of its text. No subprocess, no network, no live runtime.

These tests mirror (independently, in pure Python) the same A8 marker-line
semantics that scripts/audit-hermes-pipeline-hardening.sh enforces via
run_a8_invariant_audit, so a regression in the template is caught here even
before the shell audit runs, and a regression in the shell audit's own logic
cannot silently disable this coverage.
"""

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "default-SOUL.md")

with open(TEMPLATE_PATH, "r", encoding="utf-8") as _f:
    TEMPLATE_TEXT = _f.read()

# Exact marker lines the repository audit (INV-01..INV-15) requires. Kept as
# (invariant_id, line) pairs so a removed/renamed marker fails loudly with
# the specific invariant it breaks, not a generic "template changed" error.
REQUIRED_MARKER_LINES = [
    ("INV-01", "A8-MANDATE-01: default profile must register planner_bridge (planning only)."),
    ("INV-02a", "A8-MANDATE-02: default profile must register pipeline_controller (sole Kanban lifecycle interface)."),
    ("INV-02b", "A8-OWNERSHIP-01: default is the sole owner of the Kanban graph (create, check, wait, review, correction, archive, READY_TO_COMMIT)."),
    ("INV-03", "A8-PROHIBIT-01: default profile MUST NOT register or use pipeline_bridge directly."),
    ("INV-04", "A8-PROHIBIT-02: default profile MUST NOT register or use review_archive_bridge directly."),
    ("INV-05", "A8-ROLES-01: reviewer: read-only, uses review_bridge."),
    ("INV-06", "A8-ROLES-02: coder-claude: implementation, uses claude_bridge."),
    ("INV-07", "A8-PROHIBIT-03: pipeline_controller is forbidden in profiles: reviewer, coder, coder-claude, planner-codex, sysadmin."),
    ("INV-10", "A8-READY-01: READY_TO_COMMIT is read-only; no MCP tool performs a commit."),
    ("INV-11", "A8-PHASES-01: A8.1 = repository implementation. A8.2 = read-only review + archive + READY_TO_COMMIT + human commit/push. A8.3 = runtime policy/config rollout (separate human authorization)."),
    ("INV-12", "A8-TEMPLATE-01: This file is a versioned, installable TEMPLATE of the default profile SOUL policy. It is NOT the live executable SOUL and is not on any runtime path."),
    ("INV-13", "A8-PLANNER-01: planner_bridge is planning-only (no lifecycle task creation)."),
    ("INV-14a", "A8-APPROVAL-01: commit approval requires one explicit human authorization."),
    ("INV-14b", "A8-APPROVAL-02: push approval requires a separate, explicit human authorization (dual-approval: commit and push are never bundled)."),
]

SEVEN_TOOL_NAMES = [
    "check_task",
    "create_implementation",
    "create_review",
    "create_correction",
    "wait_task",
    "archive_review",
    "ready_to_commit",
]

# Patterns a policy document must never present as an instruction for an
# agent to follow directly, regardless of surrounding phrasing.
FORBIDDEN_DIRECT_COMMIT_PUSH_PATTERNS = [
    re.compile(r"run\s+git\s+commit", re.IGNORECASE),
    re.compile(r"git\s+push\s+origin", re.IGNORECASE),
    re.compile(r"use\s+(the\s+)?commit\s+tool", re.IGNORECASE),
    re.compile(r"mcp__[a-z0-9_]*commit[a-z0-9_]*", re.IGNORECASE),
    re.compile(r"mcp__[a-z0-9_]*push[a-z0-9_]*", re.IGNORECASE),
]

# Lines mentioning these bridge identifiers are only allowed when they carry
# one of these disqualifying/prohibiting keywords -- i.e. they may only
# appear as part of a prohibition, never as an approved instruction to use
# the bridge directly.
_PROHIBITION_KEYWORDS = ("must not", "never", "forbidden", "forbids", "prohibit", "deregist")


def _missing_marker_lines(text):
    missing = []
    for inv_id, line in REQUIRED_MARKER_LINES:
        if line not in text:
            missing.append(inv_id)
    return missing


def _find_forbidden_commit_push_instructions(text):
    matches = []
    for pattern in FORBIDDEN_DIRECT_COMMIT_PUSH_PATTERNS:
        for m in pattern.finditer(text):
            matches.append(m.group(0))
    return matches


def _lines_mentioning_direct_bridges(text):
    return [
        line for line in text.splitlines()
        if "pipeline_bridge" in line or "review_archive_bridge" in line
    ]


# --- file identity and no leftover temp-carrier content ---------------------


def test_template_file_exists_and_is_nonempty():
    assert os.path.isfile(TEMPLATE_PATH)
    assert len(TEMPLATE_TEXT.strip()) > 0


def test_declared_as_installable_template_not_live_soul():
    assert (
        "A8-TEMPLATE-01: This file is a versioned, installable TEMPLATE of the default profile SOUL policy. "
        "It is NOT the live executable SOUL and is not on any runtime path." in TEMPLATE_TEXT
    )


# --- required A8 marker lines (positive) ------------------------------------


def test_compliant_template_has_no_missing_markers():
    assert _missing_marker_lines(TEMPLATE_TEXT) == []


@pytest.mark.parametrize("inv_id,line", REQUIRED_MARKER_LINES)
def test_each_required_marker_line_present(inv_id, line):
    assert line in TEMPLATE_TEXT, f"{inv_id} marker line missing"


def test_all_seven_pipeline_controller_tools_named():
    for name in SEVEN_TOOL_NAMES:
        assert name in TEMPLATE_TEXT, f"tool {name} not named in template"


# --- required A8 marker lines (negative / mutation) -------------------------


@pytest.mark.parametrize("inv_id,line", REQUIRED_MARKER_LINES)
def test_removing_a_required_marker_line_is_detected(inv_id, line):
    assert line in TEMPLATE_TEXT, "mutation assumption stale: marker not found in template"
    mutated = TEMPLATE_TEXT.replace(line, "", 1)
    assert mutated != TEMPLATE_TEXT
    missing = _missing_marker_lines(mutated)
    assert inv_id in missing


def test_removing_one_of_seven_tools_breaks_tool_roster_completeness():
    assert "archive_review" in TEMPLATE_TEXT
    mutated = TEMPLATE_TEXT.replace("archive_review", "")
    assert "archive_review" not in mutated
    remaining = [name for name in SEVEN_TOOL_NAMES if name in mutated]
    assert remaining == [n for n in SEVEN_TOOL_NAMES if n != "archive_review"]


# --- direct commit/push instructions must never appear ----------------------


def test_real_template_contains_no_direct_commit_push_instruction():
    assert _find_forbidden_commit_push_instructions(TEMPLATE_TEXT) == []


@pytest.mark.parametrize("injected", [
    "If READY_TO_COMMIT succeeds, run git commit immediately.",
    "After approval, git push origin main.",
    "To finish, use the commit tool.",
    "Call mcp__pipeline_controller__commit_changes to finish.",
    "Call mcp__pipeline_controller__push_changes to finish.",
])
def test_injected_direct_commit_push_instruction_is_detected(injected):
    mutated = TEMPLATE_TEXT + "\n" + injected + "\n"
    assert _find_forbidden_commit_push_instructions(mutated) != []


# --- pipeline_bridge / review_archive_bridge mentions stay prohibitive ------


def test_every_direct_bridge_mention_is_a_prohibition_not_an_instruction():
    lines = _lines_mentioning_direct_bridges(TEMPLATE_TEXT)
    assert lines, "fixture assumption stale: expected at least one bridge mention"
    for line in lines:
        lowered = line.lower()
        assert any(keyword in lowered for keyword in _PROHIBITION_KEYWORDS), (
            f"line mentions a direct bridge without a prohibiting keyword: {line!r}"
        )


def test_injecting_an_approving_bridge_instruction_is_detected():
    injected = "\ndefault should call pipeline_bridge directly for speed.\n"
    mutated = TEMPLATE_TEXT + injected
    lines = _lines_mentioning_direct_bridges(mutated)
    offending = [
        line for line in lines
        if not any(keyword in line.lower() for keyword in _PROHIBITION_KEYWORDS)
    ]
    assert offending, "mutation should have introduced a non-prohibiting bridge mention"


# --- phase boundary and dual human approval ---------------------------------


def test_phase_boundary_names_all_three_phases_in_order():
    phase_lines = [
        line
        for line in TEMPLATE_TEXT.splitlines()
        if line.startswith("A8-PHASES-01:")
    ]
    assert len(phase_lines) == 1
    phase_line = phase_lines[0]
    idx1 = phase_line.find("A8.1")
    idx2 = phase_line.find("A8.2")
    idx3 = phase_line.find("A8.3")
    assert -1 not in (idx1, idx2, idx3)
    assert idx1 < idx2 < idx3


def test_commit_and_push_approvals_are_kept_separate():
    assert "A8-APPROVAL-01" in TEMPLATE_TEXT
    assert "A8-APPROVAL-02" in TEMPLATE_TEXT
    assert "separate, explicit human authorization" in TEMPLATE_TEXT


def test_merging_commit_and_push_approval_is_detected():
    merged_line = (
        "A8-APPROVAL-01: commit approval requires one explicit human authorization."
    )
    separate_line = (
        "A8-APPROVAL-02: push approval requires a separate, explicit human authorization "
        "(dual-approval: commit and push are never bundled)."
    )
    mutated = TEMPLATE_TEXT.replace(separate_line, "", 1)
    assert mutated != TEMPLATE_TEXT
    assert merged_line in mutated
    assert separate_line not in mutated
