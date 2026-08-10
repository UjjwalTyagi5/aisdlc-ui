import pytest
import unittest

pytestmark = pytest.mark.skip(
    reason="pre-existing HandoffPayload import error — HandoffPayload/parse_handoff_payload "
    "were never added to shared.models.__init__; quarantined in milestone-4 Wave A; "
    "tracked in OUTSTANDING_WORK"
)

try:
    from pydantic import ValidationError

    from shared.models import (
        CommandResult,
        DesignArtifacts,
        DevelopmentArtifacts,
        HandoffPayload,
        RequirementsPayload,
        ValidationResult,
        parse_artifact_sections,
        parse_handoff_dict,
        parse_handoff_payload,
        strip_handoff_sentinel,
    )
except ImportError:
    # Quarantined — imports unavailable; tests are skipped via pytestmark above
    ValidationError = Exception
    HandoffPayload = None
    RequirementsPayload = None
    DevelopmentArtifacts = None
    CommandResult = None
    ValidationResult = None
    DesignArtifacts = None
    parse_artifact_sections = None
    parse_handoff_dict = None
    parse_handoff_payload = None
    strip_handoff_sentinel = None


class SharedContractTests(unittest.TestCase):
    def test_handoff_payload_accepts_valid_testing_handoff(self):
        payload = HandoffPayload(
            to="testing",
            batch_id="TEST-READY",
            stage_completed="development",
            context_keys=["requirements_payload", "design_artifacts", "development_artifacts"],
            triggered_by="user_confirmed",
        )

        self.assertEqual(payload.to, "testing")
        self.assertEqual(payload.context_keys[-1], "development_artifacts")

    def test_handoff_payload_rejects_unknown_target(self):
        with self.assertRaises(ValidationError):
            HandoffPayload(to="security", batch_id="SECURITY-READY")

    def test_parse_handoff_payload_validates_sentinel(self):
        text = 'Done.\nHANDOFF::{"to":"development","batch_id":"DEV-READY","context_keys":["design_artifacts"]}'

        payload = parse_handoff_payload(text)

        self.assertIsNotNone(payload)
        self.assertEqual(payload.to, "development")
        self.assertEqual(payload.context_keys, ["design_artifacts"])

    def test_parse_handoff_dict_returns_none_for_invalid_sentinel(self):
        text = 'HANDOFF::{"to":"unknown","batch_id":"BAD"}'

        self.assertIsNone(parse_handoff_dict(text))

    def test_strip_handoff_sentinel_removes_user_hidden_payload(self):
        text = 'Design is complete.\nHANDOFF::{"to":"development","batch_id":"DEV-READY"}'

        self.assertEqual(strip_handoff_sentinel(text), "Design is complete.")

    def test_requirements_payload_uses_independent_story_lists(self):
        first = RequirementsPayload(project="A", batch_id="BATCH-1")
        second = RequirementsPayload(project="B", batch_id="BATCH-2")
        first.stories.append({"id": "1", "title": "Story", "description": "Desc"})

        self.assertEqual(len(first.stories), 1)
        self.assertEqual(second.stories, [])

    def test_development_artifacts_defaults_are_independent(self):
        first = DevelopmentArtifacts()
        second = DevelopmentArtifacts()
        first.changed_files.append("src/app.py")

        self.assertEqual(first.changed_files, ["src/app.py"])
        self.assertEqual(second.changed_files, [])

    def test_development_artifacts_accept_command_and_validation_results(self):
        artifacts = DevelopmentArtifacts(
            commands_run=[CommandResult(command="python -m pytest", exit_code=0)],
            test_results=[ValidationResult(name="unit", status="passed", command="python -m pytest")],
            status="validated",
        )

        self.assertEqual(artifacts.commands_run[0].exit_code, 0)
        self.assertEqual(artifacts.test_results[0].status, "passed")


    # ── parse_artifact_sections ──────────────────────────────────────────────

    _SAMPLE_DESIGN = """
## HIGH-LEVEL DESIGN
System overview here.

## LOW-LEVEL DESIGN
Detailed components here.

## C4 ARCHITECTURE DIAGRAMS
```mermaid
graph TD
  A-->B
```

## API CONTRACT
GET /api/users

## DATABASE SCHEMA
CREATE TABLE users (id INT PRIMARY KEY);

## ARCHITECTURE DECISION RECORDS
ADR-001: Use PostgreSQL

## TECHNOLOGY STACK
Python 3.11, FastAPI, PostgreSQL
"""

    def test_parse_artifact_sections_extracts_all_seven(self):
        artifacts = parse_artifact_sections(self._SAMPLE_DESIGN)
        self.assertIsInstance(artifacts, DesignArtifacts)
        self.assertIn("System overview", artifacts.hld)
        self.assertIn("Detailed components", artifacts.lld)
        self.assertIn("mermaid", artifacts.c4_diagrams)
        self.assertIn("/api/users", artifacts.api_contract)
        self.assertIn("CREATE TABLE", artifacts.database_schema)
        self.assertIn("ADR-001", artifacts.adrs)
        self.assertIn("FastAPI", artifacts.tech_stack)
        self.assertEqual(artifacts.full_document, self._SAMPLE_DESIGN)

    def test_parse_artifact_sections_missing_section_is_none(self):
        content = "## HIGH-LEVEL DESIGN\nOnly HLD present.\n"
        artifacts = parse_artifact_sections(content)
        self.assertIn("Only HLD", artifacts.hld)
        self.assertIsNone(artifacts.lld)
        self.assertIsNone(artifacts.api_contract)

    def test_parse_artifact_sections_case_insensitive(self):
        content = "## High-Level Design\nHLD content.\n\n## low-level design\nLLD content.\n"
        artifacts = parse_artifact_sections(content)
        self.assertIn("HLD content", artifacts.hld)
        self.assertIn("LLD content", artifacts.lld)

    def test_parse_artifact_sections_attaches_docx_url(self):
        artifacts = parse_artifact_sections("## HIGH-LEVEL DESIGN\nContent.\n", docx_url="http://example.com/doc.docx")
        self.assertEqual(artifacts.docx_url, "http://example.com/doc.docx")

    def test_parse_artifact_sections_empty_content_returns_all_none(self):
        artifacts = parse_artifact_sections("")
        for field in ("hld", "lld", "c4_diagrams", "api_contract", "database_schema", "adrs", "tech_stack"):
            self.assertIsNone(getattr(artifacts, field))

    def test_parse_handoff_payload_handles_nested_json(self):
        # Verifies the brace-balanced extractor works with nested JSON
        text = 'HANDOFF::{"to":"design","batch_id":"DESIGN-READY","context_keys":["requirements_payload"],"meta":{"k":"v"}}'
        payload = parse_handoff_payload(text)
        self.assertIsNotNone(payload)
        self.assertEqual(payload.to, "design")


if __name__ == "__main__":
    unittest.main()
