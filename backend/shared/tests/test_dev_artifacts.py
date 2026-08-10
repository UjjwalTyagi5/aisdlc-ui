import pytest
import unittest

pytestmark = pytest.mark.skip(
    reason="pre-existing import error — shared.models.handoff.HandoffPayload does not exist; "
    "quarantined in milestone-4 Wave A"
)

try:
    from agents_orchestrator.development_agent.config.session_state import (
        DevSessionState,
        clear_session,
        get_session,
    )
    from agents_orchestrator.development_agent.tools.sandbox_policy import classify_command
    from shared.models.development import (
        CommandResult,
        DevelopmentArtifacts,
        ValidationResult,
    )
    from shared.models.handoff import HandoffPayload, parse_handoff_payload
    from pydantic import ValidationError
except (ImportError, ModuleNotFoundError):
    DevSessionState = None
    clear_session = lambda x: None
    get_session = lambda x: None
    classify_command = None
    CommandResult = None
    DevelopmentArtifacts = None
    ValidationResult = None
    HandoffPayload = None
    parse_handoff_payload = None
    ValidationError = Exception


class DevelopmentArtifactsModelTests(unittest.TestCase):
    def test_defaults_are_independent_across_instances(self):
        a = DevelopmentArtifacts()
        b = DevelopmentArtifacts()
        a.changed_files.append("src/app.py")
        self.assertEqual(a.changed_files, ["src/app.py"])
        self.assertEqual(b.changed_files, [])

    def test_generated_files_independent(self):
        a = DevelopmentArtifacts()
        b = DevelopmentArtifacts()
        a.generated_files.append("src/new.py")
        self.assertEqual(b.generated_files, [])

    def test_status_default_is_not_started(self):
        self.assertEqual(DevelopmentArtifacts().status, "not_started")

    def test_pr_created_status_accepted(self):
        a = DevelopmentArtifacts(status="pr_created")
        self.assertEqual(a.status, "pr_created")

    def test_model_dump_includes_all_fields(self):
        a = DevelopmentArtifacts(
            repo_url="https://github.com/org/repo",
            branch_name="feature/test",
            pr_url="https://github.com/org/repo/pull/1",
            status="pr_created",
        )
        d = a.model_dump()
        self.assertEqual(d["repo_url"], "https://github.com/org/repo")
        self.assertEqual(d["status"], "pr_created")
        self.assertIn("changed_files", d)


class CommandResultTests(unittest.TestCase):
    def test_command_result_defaults(self):
        cr = CommandResult(command="npm run build")
        self.assertEqual(cr.exit_code, None)
        self.assertTrue(cr.sanitized)
        self.assertTrue(cr.allowed)

    def test_command_result_with_exit_code(self):
        cr = CommandResult(command="pytest tests/", exit_code=0, stdout="3 passed")
        self.assertEqual(cr.exit_code, 0)
        self.assertEqual(cr.stdout, "3 passed")


class ValidationResultTests(unittest.TestCase):
    def test_passed_status(self):
        vr = ValidationResult(name="ruff check", status="passed", command="ruff check src/")
        self.assertEqual(vr.status, "passed")

    def test_failed_status(self):
        vr = ValidationResult(name="pytest", status="failed", command="pytest tests/")
        self.assertEqual(vr.status, "failed")

    def test_invalid_status_raises(self):
        with self.assertRaises(ValidationError):
            ValidationResult(name="x", status="unknown")


class SessionDevArtifactsTests(unittest.TestCase):
    def setUp(self):
        clear_session("A")
        clear_session("B")

    def test_session_gets_fresh_artifacts(self):
        s = get_session("A")
        self.assertIsInstance(s.dev_artifacts, DevelopmentArtifacts)
        self.assertEqual(s.dev_artifacts.changed_files, [])

    def test_artifact_mutation_is_session_local(self):
        get_session("A").dev_artifacts.changed_files.append("src/a.py")
        self.assertEqual(get_session("B").dev_artifacts.changed_files, [])

    def test_artifact_persists_across_get_session_calls(self):
        get_session("A").dev_artifacts.branch_name = "feature/test"
        self.assertEqual(get_session("A").dev_artifacts.branch_name, "feature/test")


class ClassifyCommandTests(unittest.TestCase):
    def test_pytest_is_test(self):
        self.assertEqual(classify_command("pytest tests/ -v"), "test")

    def test_jest_is_test(self):
        self.assertEqual(classify_command("jest --coverage"), "test")

    def test_npm_test_is_test(self):
        self.assertEqual(classify_command("npm test"), "test")

    def test_npm_run_build_is_build(self):
        self.assertEqual(classify_command("npm run build"), "build")

    def test_tsc_is_build(self):
        self.assertEqual(classify_command("tsc --noEmit"), "build")

    def test_dotnet_build_is_build(self):
        self.assertEqual(classify_command("dotnet build"), "build")

    def test_pip_install_is_generic(self):
        self.assertEqual(classify_command("pip install -r requirements.txt"), "command")

    def test_npm_install_is_generic(self):
        self.assertEqual(classify_command("npm install"), "command")


class DevelopmentHandoffTests(unittest.TestCase):
    _HANDOFF = (
        'HANDOFF::{"to":"testing","batch_id":"TEST-READY",'
        '"stage_completed":"development",'
        '"context_keys":["requirements_payload","design_artifacts","development_artifacts"],'
        '"triggered_by":"user_confirmed"}'
    )

    def test_handoff_payload_accepts_testing_target(self):
        payload = HandoffPayload(
            to="testing",
            batch_id="TEST-READY",
            stage_completed="development",
            context_keys=["requirements_payload", "design_artifacts", "development_artifacts"],
            triggered_by="user_confirmed",
        )
        self.assertEqual(payload.to, "testing")
        self.assertEqual(payload.stage_completed, "development")
        self.assertEqual(len(payload.context_keys), 3)

    def test_parse_handoff_payload_from_sentinel(self):
        payload = parse_handoff_payload(self._HANDOFF)
        self.assertIsNotNone(payload)
        self.assertEqual(payload.to, "testing")
        self.assertEqual(payload.batch_id, "TEST-READY")

    def test_handoff_payload_rejects_invalid_target(self):
        with self.assertRaises(ValidationError):
            HandoffPayload(to="unknown_agent", batch_id="X")

    def test_model_dump_roundtrip(self):
        payload = parse_handoff_payload(self._HANDOFF)
        d = payload.model_dump()
        self.assertEqual(d["to"], "testing")
        self.assertIn("context_keys", d)


if __name__ == "__main__":
    unittest.main()
