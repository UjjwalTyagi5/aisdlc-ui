"""Pins the mirror between backend routing.py and frontend requests/routing.ts —
the docstring in routing.py has claimed this file exists since before this test
was written; a mismatch here is a silent 403 in production, found this way once
already (mcp_server absent from _BU_ADMIN_RAISABLE while present in the frontend
copy, see plan task 1)."""
from shared.governance import routing


def test_bu_admin_raisable_includes_mcp_server():
    """mcp_server is tier-routed like connector_access, its closest sibling —
    a Business Unit Admin who lacks an MCP server must be able to ask for one,
    exactly as they already can for a connector."""
    assert "mcp_server" in routing.raisable_types_for("bu_admin")


def test_every_raisable_list_only_names_real_types():
    """A typo in any *_RAISABLE tuple would silently make a type unraisable by
    anyone rather than erroring — this is the cheap net for that."""
    for role in ("bu_admin", "project_admin", None):
        for t in routing.raisable_types_for(role):
            assert t in routing.REQUEST_TYPES, f"{t!r} in raisable_types_for({role!r}) is not a real REQUEST_TYPES entry"
