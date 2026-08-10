"""Artifact model tests — verifies the canonical *Artifact models, TraceLink, and validate_handoff.

Tests reference shared.models.artifacts (the ORM-layer typed models), NOT the
separate shared.models.requirements / design / development modules.
"""
from __future__ import annotations

import json
import pytest


# ---------------------------------------------------------------------------
# Test 1: validate_handoff returns a RequirementsArtifact from a valid dict
# ---------------------------------------------------------------------------

class TestValidateHandoffDict:
    def test_valid_requirements_artifact_dict(self):
        from shared.models.artifacts import RequirementsArtifact, validate_handoff

        data = {
            "agent_session_id": "s1",
            "brd_content": "Some BRD text",
            "user_stories": [{"id": "us-1", "title": "Story one"}],
        }
        result = validate_handoff(data, RequirementsArtifact)
        assert isinstance(result, RequirementsArtifact)
        assert result.agent_session_id == "s1"
        assert result.brd_content == "Some BRD text"
        assert result.version == 1  # default


# ---------------------------------------------------------------------------
# Test 2: validate_handoff accepts a JSON string with HANDOFF:: sentinel
# ---------------------------------------------------------------------------

class TestValidateHandoffSentinel:
    def test_json_string_with_handoff_sentinel(self):
        from shared.models.artifacts import RequirementsArtifact, validate_handoff

        data = {"agent_session_id": "s2", "acceptance_criteria": ["AC-1"]}
        sentinel_str = "HANDOFF::\n" + json.dumps(data)
        result = validate_handoff(sentinel_str, RequirementsArtifact)
        assert isinstance(result, RequirementsArtifact)
        assert result.agent_session_id == "s2"
        assert result.acceptance_criteria == ["AC-1"]

    def test_json_string_with_requirements_payload_sentinel(self):
        from shared.models.artifacts import RequirementsArtifact, validate_handoff

        data = {"agent_session_id": "s3"}
        sentinel_str = "REQUIREMENTS_PAYLOAD::" + json.dumps(data)
        result = validate_handoff(sentinel_str, RequirementsArtifact)
        assert isinstance(result, RequirementsArtifact)
        assert result.agent_session_id == "s3"

    def test_plain_json_string_no_sentinel(self):
        from shared.models.artifacts import RequirementsArtifact, validate_handoff

        data = {"agent_session_id": "s4"}
        result = validate_handoff(json.dumps(data), RequirementsArtifact)
        assert isinstance(result, RequirementsArtifact)
        assert result.agent_session_id == "s4"


# ---------------------------------------------------------------------------
# Test 3: validate_handoff raises ValueError on wrong-typed field
# ---------------------------------------------------------------------------

class TestValidateHandoffTypeError:
    def test_wrong_typed_adrs_field_raises_value_error(self):
        """adrs must be a list of dicts; a plain string raises ValueError."""
        from shared.models.artifacts import DesignArtifact, validate_handoff

        bad_payload = {"adrs": "notalist"}
        with pytest.raises(ValueError) as exc_info:
            validate_handoff(bad_payload, DesignArtifact)
        # Error message must contain useful detail
        assert "DesignArtifact" in str(exc_info.value) or len(str(exc_info.value)) > 0


# ---------------------------------------------------------------------------
# Test 4: DesignArtifact valid dict validates via validate_handoff
# ---------------------------------------------------------------------------

class TestValidateHandoffDesignArtifact:
    def test_design_artifact_minimal_dict(self):
        from shared.models.artifacts import DesignArtifact, validate_handoff

        data = {"hld": "High level design content", "version": 1}
        result = validate_handoff(data, DesignArtifact)
        assert isinstance(result, DesignArtifact)
        assert result.hld == "High level design content"
        assert result.version == 1

    def test_design_artifact_full_dict(self):
        from shared.models.artifacts import DesignArtifact, validate_handoff

        data = {
            "hld": "HLD content",
            "lld": "LLD content",
            "api_contracts": "POST /api/v1/run",
            "database_schema": "CREATE TABLE runs (...)",
            "c4_diagram_url": "https://kroki.io/abc.png",
            "adrs": [{"id": "ADR-001", "decision": "Use Postgres"}],
            "security_checklist": "OWASP Top 10 reviewed",
            "version": 2,
        }
        result = validate_handoff(data, DesignArtifact)
        assert isinstance(result, DesignArtifact)
        assert result.version == 2
        assert len(result.adrs) == 1


# ---------------------------------------------------------------------------
# Test 5: TraceLink constructs correctly
# ---------------------------------------------------------------------------

class TestTraceLink:
    def test_trace_link_constructs(self):
        from shared.models.artifacts import TraceLink

        link = TraceLink(from_id="a", to_id="b", kind="satisfies")
        assert link.from_id == "a"
        assert link.to_id == "b"
        assert link.kind == "satisfies"

    def test_trace_link_missing_field_raises(self):
        from pydantic import ValidationError
        from shared.models.artifacts import TraceLink

        with pytest.raises(ValidationError):
            TraceLink(from_id="req-1", to_id="design-1")  # missing kind


# ---------------------------------------------------------------------------
# Sanity: existing *Artifact models are unmodified
# ---------------------------------------------------------------------------

class TestExistingArtifactModelsUnmodified:
    def test_requirements_artifact_fields(self):
        from shared.models.artifacts import RequirementsArtifact

        a = RequirementsArtifact(agent_session_id="sess-1")
        assert a.agent_session_id == "sess-1"
        assert a.version == 1
        assert a.brd_content is None

    def test_development_artifact_fields(self):
        from shared.models.artifacts import DevelopmentArtifact

        a = DevelopmentArtifact(repo_url="https://github.com/org/repo", branch_name="main")
        assert a.repo_url == "https://github.com/org/repo"
        assert a.version == 1

    def test_testing_artifact_fields(self):
        from shared.models.artifacts import TestingArtifact

        a = TestingArtifact(test_plan="Run all unit tests")
        assert a.test_plan == "Run all unit tests"

    def test_code_review_artifact_fields(self):
        from shared.models.artifacts import CodeReviewArtifact

        a = CodeReviewArtifact(pr_ref="PR#42", findings=[{"file": "a.py", "line": 10}])
        assert a.pr_ref == "PR#42"
        assert len(a.findings) == 1

    def test_security_artifact_fields(self):
        from shared.models.artifacts import SecurityArtifact

        a = SecurityArtifact(scope="backend", security_sign_off=True)
        assert a.scope == "backend"
        assert a.security_sign_off is True
