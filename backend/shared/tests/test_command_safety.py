import unittest

from agents_orchestrator.development_agent.tools.sandbox_policy import (
    DEFAULT_POLICY,
    SandboxPolicy,
    sanitize_output,
    validate_command,
)


class ValidateCommandTests(unittest.TestCase):
    def test_allowed_npm_command_passes(self):
        self.assertIsNone(validate_command("npm run build"))

    def test_allowed_pytest_command_passes(self):
        self.assertIsNone(validate_command("pytest tests/ -v"))

    def test_allowed_python_command_passes(self):
        self.assertIsNone(validate_command("python manage.py migrate"))

    def test_pipe_is_rejected(self):
        err = validate_command("npm run build | tee output.txt")
        self.assertIsNotNone(err)
        self.assertIn("shell operator", err)

    def test_semicolon_is_rejected(self):
        err = validate_command("npm install; rm -rf /")
        self.assertIsNotNone(err)
        self.assertIn("shell operator", err)

    def test_and_chaining_is_rejected(self):
        err = validate_command("npm install && npm run build")
        self.assertIsNotNone(err)
        self.assertIn("shell operator", err)

    def test_or_chaining_is_rejected(self):
        err = validate_command("npm run test || echo failed")
        self.assertIsNotNone(err)
        self.assertIn("shell operator", err)

    def test_backtick_is_rejected(self):
        err = validate_command("python `which python3`")
        self.assertIsNotNone(err)
        self.assertIn("shell operator", err)

    def test_command_substitution_is_rejected(self):
        err = validate_command("npm install $(cat requirements.txt)")
        self.assertIsNotNone(err)
        self.assertIn("shell operator", err)

    def test_newline_injection_is_rejected(self):
        err = validate_command("npm run build\nrm -rf /")
        self.assertIsNotNone(err)
        self.assertIn("shell operator", err)

    def test_allow_shell_operators_policy_bypasses_check(self):
        policy = SandboxPolicy(allow_shell_operators=True)
        self.assertIsNone(validate_command("npm run build | tee out.txt", policy))


class SanitizeOutputTests(unittest.TestCase):
    def test_output_within_cap_is_unchanged(self):
        text = "All tests passed"
        self.assertEqual(sanitize_output(text), text)

    def test_output_exceeding_cap_is_truncated(self):
        policy = SandboxPolicy(output_cap_bytes=20)
        text = "A" * 100
        result = sanitize_output(text, policy)
        self.assertIn("[output capped", result)
        self.assertLessEqual(len(result.encode()), 20 + 60)  # cap + notice

    def test_password_in_output_is_redacted(self):
        text = "Connected with password=SuperSecret123"
        result = sanitize_output(text)
        self.assertNotIn("SuperSecret123", result)
        self.assertIn("***", result)

    def test_bearer_token_is_redacted(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = sanitize_output(text)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", result)

    def test_github_pat_is_redacted(self):
        text = "Token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefg"
        result = sanitize_output(text)
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefg", result)
        self.assertIn("ghp_***", result)

    def test_sdk_key_is_redacted(self):
        text = "Using key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        result = sanitize_output(text)
        self.assertNotIn("sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890", result)

    def test_credential_in_clone_url_is_redacted(self):
        text = "Cloning https://username:ghp_secret_token@dev.azure.com/org/project"
        result = sanitize_output(text)
        self.assertNotIn("ghp_secret_token", result)

    def test_redact_secrets_false_leaves_content_intact(self):
        policy = SandboxPolicy(redact_secrets=False)
        text = "password=hunter2"
        self.assertEqual(sanitize_output(text, policy), text)

    def test_normal_build_output_is_not_redacted(self):
        text = "BUILD SUCCESSFUL in 3s\n8 actionable tasks: 8 executed"
        result = sanitize_output(text)
        self.assertEqual(result, text)


class SandboxPolicyDefaultsTests(unittest.TestCase):
    def test_bash_disabled_by_default(self):
        self.assertFalse(DEFAULT_POLICY.allow_bash)

    def test_shell_operators_disabled_by_default(self):
        self.assertFalse(DEFAULT_POLICY.allow_shell_operators)

    def test_redact_secrets_enabled_by_default(self):
        self.assertTrue(DEFAULT_POLICY.redact_secrets)

    def test_output_cap_is_set(self):
        self.assertGreater(DEFAULT_POLICY.output_cap_bytes, 0)


if __name__ == "__main__":
    unittest.main()
