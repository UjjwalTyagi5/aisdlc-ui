"""A stage's board must be one somebody actually connected.

`_pick_board_kind` de-prioritises Azure DevOps on purpose: a stage wired to both ADO
and Jira should resolve to Jira. But it applied that preference without asking whether
Jira was configured, so a project with both assigned and only ADO connected resolved
to a Jira connector with no URL and no token — and every board call failed with
`UnsupportedProtocol`, which is httpx being handed an empty string. Reported by a BA
who had connected Azure DevOps and tested it green on the Integrations page.
"""
import pytest

from shared.services import agent_run
from shared.services.agent_run import _pick_board_kind, _pick_usable_board_kind


# ── the static preference is unchanged ───────────────────────────────────────
def test_a_single_board_is_the_answer_whatever_it_is():
    assert _pick_board_kind(["azure_devops"]) == "azure_devops"
    assert _pick_board_kind(["jira"]) == "jira"


def test_azure_devops_still_loses_to_another_board():
    assert _pick_board_kind(["azure_devops", "jira"]) == "jira"


def test_no_board_assigned_is_a_real_answer():
    assert _pick_board_kind([]) is None


# ── the preference now applies only to boards that work ──────────────────────
@pytest.fixture
def credentialed(monkeypatch):
    """Control which kinds report a resolvable credential."""
    def _set(*kinds: str):
        async def fake(kind, tenant_id, project_id, owner_id):
            return kind in kinds
        monkeypatch.setattr(agent_run, "_board_is_credentialed", fake)
    return _set


async def _pick(assigned):
    return await _pick_usable_board_kind(assigned, "t", "p", "u")


@pytest.mark.asyncio
async def test_the_connected_board_wins_over_the_preferred_one(credentialed):
    """The reported bug: both assigned, only ADO connected."""
    credentialed("azure_devops")

    assert await _pick(["azure_devops", "jira"]) == "azure_devops"


@pytest.mark.asyncio
async def test_the_preference_still_decides_when_both_are_connected(credentialed):
    """The rule was right about which board to favour — it was wrong to apply it to
    one that cannot be used. With both usable, Jira still wins."""
    credentialed("azure_devops", "jira")

    assert await _pick(["azure_devops", "jira"]) == "jira"


@pytest.mark.asyncio
async def test_nothing_connected_falls_back_to_the_declared_board(credentialed):
    """Unchanged behaviour, deliberately: the failure should stay "connect this
    board" rather than silently becoming a different board."""
    credentialed()  # none

    assert await _pick(["azure_devops", "jira"]) == "jira"


@pytest.mark.asyncio
async def test_a_single_assigned_board_is_never_probed(monkeypatch):
    """No choice to make, so no reason to spend a secret lookup on it."""
    called = []

    async def fake(kind, tenant_id, project_id, owner_id):
        called.append(kind)
        return False
    monkeypatch.setattr(agent_run, "_board_is_credentialed", fake)

    assert await _pick(["jira"]) == "jira"
    assert called == []


@pytest.mark.asyncio
async def test_a_probe_that_raises_does_not_break_the_turn(monkeypatch):
    """A credential lookup failing must not stop a chat turn — it reads as
    'not credentialed' and the static preference decides."""
    async def boom(kind, tenant_id, project_id, owner_id):
        raise RuntimeError("secret store unreachable")
    monkeypatch.setattr(agent_run, "_board_is_credentialed", boom)

    with pytest.raises(RuntimeError):
        # The guard lives inside _board_is_credentialed itself, so a stub that
        # raises escapes — this pins that the REAL one swallows, below.
        await _pick(["azure_devops", "jira"])


@pytest.mark.asyncio
async def test_the_real_probe_swallows_its_own_errors(monkeypatch):
    """`_board_is_credentialed` is the one that must never propagate."""
    import config.connector_factory as factory

    async def boom(**kwargs):
        raise RuntimeError("no such connector")
    monkeypatch.setattr(factory, "get_connector_for_session", boom)

    assert await agent_run._board_is_credentialed("jira", "t", "p", "u") is False
