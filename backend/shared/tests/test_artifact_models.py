"""Tests for typed Pydantic artifact models (M2-02, D-11).

Verifies:
- All four artifact models are importable from shared.models.artifacts
- model_dump() returns a plain dict with the expected keys
- All fields are Optional with None defaults except version (int=1) and RequirementsArtifact.agent_session_id
- ORM Run model has current_stage and gate_pending mapped columns
"""
import unittest


class TestArtifactModelsImport(unittest.TestCase):
    def test_all_four_models_importable(self):
        from shared.models.artifacts import (  # noqa: F401
            DesignArtifact,
            DevelopmentArtifact,
            RequirementsArtifact,
            TestingArtifact,
        )


class TestRequirementsArtifact(unittest.TestCase):
    def setUp(self):
        from shared.models.artifacts import RequirementsArtifact
        self.model_cls = RequirementsArtifact

    def test_model_dump_returns_dict(self):
        artifact = self.model_cls(agent_session_id="sess-001")
        result = artifact.model_dump()
        self.assertIsInstance(result, dict)

    def test_model_dump_has_all_expected_keys(self):
        artifact = self.model_cls(agent_session_id="sess-001")
        result = artifact.model_dump()
        expected_keys = {"brd_content", "user_stories", "acceptance_criteria", "risk_register", "version", "agent_session_id"}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_default_version_is_1(self):
        artifact = self.model_cls(agent_session_id="sess-001")
        self.assertEqual(artifact.version, 1)

    def test_optional_fields_default_to_none(self):
        artifact = self.model_cls(agent_session_id="sess-001")
        self.assertIsNone(artifact.brd_content)
        self.assertIsNone(artifact.user_stories)
        self.assertIsNone(artifact.acceptance_criteria)
        self.assertIsNone(artifact.risk_register)

    def test_agent_session_id_required(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self.model_cls()


class TestDesignArtifact(unittest.TestCase):
    def setUp(self):
        from shared.models.artifacts import DesignArtifact
        self.model_cls = DesignArtifact

    def test_model_dump_returns_dict(self):
        artifact = self.model_cls()
        result = artifact.model_dump()
        self.assertIsInstance(result, dict)

    def test_model_dump_has_all_expected_keys(self):
        artifact = self.model_cls()
        result = artifact.model_dump()
        expected_keys = {
            "hld", "lld", "api_contracts", "database_schema", "c4_diagram_url", "adrs",
            "security_checklist", "version",
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_default_version_is_1(self):
        artifact = self.model_cls()
        self.assertEqual(artifact.version, 1)

    def test_all_optional_fields_default_to_none(self):
        artifact = self.model_cls()
        self.assertIsNone(artifact.hld)
        self.assertIsNone(artifact.lld)
        self.assertIsNone(artifact.api_contracts)
        self.assertIsNone(artifact.database_schema)
        self.assertIsNone(artifact.c4_diagram_url)
        self.assertIsNone(artifact.adrs)


class TestDevelopmentArtifact(unittest.TestCase):
    def setUp(self):
        from shared.models.artifacts import DevelopmentArtifact
        self.model_cls = DevelopmentArtifact

    def test_model_dump_returns_dict(self):
        artifact = self.model_cls()
        result = artifact.model_dump()
        self.assertIsInstance(result, dict)

    def test_model_dump_has_all_expected_keys(self):
        artifact = self.model_cls()
        result = artifact.model_dump()
        expected_keys = {"repo_url", "branch_name", "pr_url", "code_summary", "test_results", "version"}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_default_version_is_1(self):
        artifact = self.model_cls()
        self.assertEqual(artifact.version, 1)

    def test_all_optional_fields_default_to_none(self):
        artifact = self.model_cls()
        self.assertIsNone(artifact.repo_url)
        self.assertIsNone(artifact.branch_name)
        self.assertIsNone(artifact.pr_url)
        self.assertIsNone(artifact.code_summary)
        self.assertIsNone(artifact.test_results)


class TestTestingArtifact(unittest.TestCase):
    def setUp(self):
        from shared.models.artifacts import TestingArtifact
        self.model_cls = TestingArtifact

    def test_model_dump_returns_dict(self):
        artifact = self.model_cls()
        result = artifact.model_dump()
        self.assertIsInstance(result, dict)

    def test_model_dump_has_all_expected_keys(self):
        artifact = self.model_cls()
        result = artifact.model_dump()
        expected_keys = {"test_plan", "test_cases", "coverage_report", "defect_log", "version"}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_default_version_is_1(self):
        artifact = self.model_cls()
        self.assertEqual(artifact.version, 1)

    def test_all_optional_fields_default_to_none(self):
        artifact = self.model_cls()
        self.assertIsNone(artifact.test_plan)
        self.assertIsNone(artifact.test_cases)
        self.assertIsNone(artifact.coverage_report)
        self.assertIsNone(artifact.defect_log)


class TestOrmRunColumns(unittest.TestCase):
    def test_run_has_current_stage_column(self):
        from shared.models.orm import Run
        column_keys = [c.key for c in Run.__table__.columns]
        self.assertIn("current_stage", column_keys)

    def test_run_has_gate_pending_column(self):
        from shared.models.orm import Run
        column_keys = [c.key for c in Run.__table__.columns]
        self.assertIn("gate_pending", column_keys)

    def test_current_stage_is_nullable(self):
        from shared.models.orm import Run
        col = Run.__table__.c["current_stage"]
        self.assertTrue(col.nullable)

    def test_gate_pending_is_not_nullable(self):
        from shared.models.orm import Run
        col = Run.__table__.c["gate_pending"]
        self.assertFalse(col.nullable)


if __name__ == "__main__":
    unittest.main()
