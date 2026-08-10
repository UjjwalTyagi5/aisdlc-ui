import os
import tempfile
import unittest

from agents_orchestrator.development_agent.config.session_state import (
    DevSessionState,
    clear_session,
    get_session,
    reset_session,
)
from agents_orchestrator.development_agent.tools.path_guard import (
    PathTraversalError,
    resolve_safe_path,
    validate_workspace_path,
)


class DevSessionStateTests(unittest.TestCase):
    def setUp(self):
        clear_session("A")
        clear_session("B")

    def test_sessions_are_independent(self):
        get_session("A").work_dir = "/tmp/a"
        get_session("B").work_dir = "/tmp/b"

        self.assertEqual(get_session("A").work_dir, "/tmp/a")
        self.assertEqual(get_session("B").work_dir, "/tmp/b")

    def test_get_session_creates_default_state(self):
        s = get_session("NEW-SESSION")
        self.assertIsInstance(s, DevSessionState)
        self.assertEqual(s.work_dir, "")
        self.assertEqual(s.build_attempts, 0)
        self.assertFalse(s.system_injected)
        clear_session("NEW-SESSION")

    def test_get_session_returns_same_object(self):
        s1 = get_session("A")
        s1.pat = "secret"
        s2 = get_session("A")
        self.assertIs(s1, s2)
        self.assertEqual(s2.pat, "secret")

    def test_reset_session_clears_state(self):
        s = get_session("A")
        s.work_dir = "/tmp/repo"
        s.build_attempts = 5
        reset_session("A")
        fresh = get_session("A")
        self.assertEqual(fresh.work_dir, "")
        self.assertEqual(fresh.build_attempts, 0)

    def test_clear_session_removes_entry(self):
        get_session("A").work_dir = "/tmp/a"
        clear_session("A")
        fresh = get_session("A")
        self.assertEqual(fresh.work_dir, "")

    def test_system_injected_flag_is_per_session(self):
        get_session("A").system_injected = True
        self.assertFalse(get_session("B").system_injected)


class PathGuardTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def test_resolve_safe_path_normal(self):
        p = resolve_safe_path(self.workspace, "src/app.py")
        self.assertTrue(str(p).startswith(self.workspace))
        self.assertTrue(str(p).endswith("app.py"))

    def test_resolve_safe_path_rejects_traversal(self):
        with self.assertRaises(PathTraversalError):
            resolve_safe_path(self.workspace, "../etc/passwd")

    def test_resolve_safe_path_rejects_double_traversal(self):
        with self.assertRaises(PathTraversalError):
            resolve_safe_path(self.workspace, "subdir/../../secret")

    def test_resolve_safe_path_redirects_absolute_inside_workspace(self):
        # lstrip strips the leading slash so /etc/passwd → workspace/etc/passwd (safe)
        p = resolve_safe_path(self.workspace, "/etc/passwd")
        self.assertTrue(str(p).startswith(os.path.realpath(self.workspace)))

    def test_resolve_safe_path_strips_leading_slash(self):
        p = resolve_safe_path(self.workspace, "/src/main.py")
        self.assertTrue(str(p).startswith(os.path.realpath(self.workspace)))

    def test_validate_workspace_path_accepts_inside(self):
        inside = os.path.join(self.workspace, "subdir", "file.py")
        p = validate_workspace_path(self.workspace, inside)
        self.assertEqual(str(p), os.path.realpath(inside))

    def test_validate_workspace_path_rejects_outside(self):
        outside = tempfile.mkdtemp()
        try:
            with self.assertRaises(PathTraversalError):
                validate_workspace_path(self.workspace, outside)
        finally:
            os.rmdir(outside)


if __name__ == "__main__":
    unittest.main()
