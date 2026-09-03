"""A Figma "Default file" that is not a file must be refused at the door.

From a live session. The Design agent answered:

    ERROR: No Figma file specified and no default file is configured for this tenant.

The project HAD a Figma credential, saved through the project Integrations dialog,
whose "Default file" field contained `srk02804@gmail.com`. That is the field the
connector reads as the default file — `FigmaConnector.auth_adapter` does
`extract_file_key(override.account)` — so an email stored cleanly, read back as no key
at all, and surfaced a session later as a message about a TENANT setting, pointing
away from the project field somebody had actually filled in.

The tenant-level route already rejected this with a 422
(`connectors._store_figma_credentials`). The project-level route did not, so the two
doors onto the same setting disagreed about what counted as valid. That disagreement
is the bug; the email was just how it showed up.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# -- what extract_file_key accepts, which is what the validation delegates to --


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        "https://www.figma.com/design/abc123XYZ/Product",
        "https://www.figma.com/file/abc123XYZ/Product",
        "AbCdEf123456789",   # a bare key: 10+ alphanumerics, no URL
    ],
)
def test_real_figma_references_are_accepted(value):
    from config.connectors.figma import extract_file_key

    assert extract_file_key(value) != ""


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        "srk02804@gmail.com",          # the actual value that caused this
        "you@company.com",
        "https://example.com/not-figma",
    ],
)
def test_things_that_are_not_a_figma_file_are_rejected(value):
    from config.connectors.figma import extract_file_key

    assert extract_file_key(value) == ""


# -- the project route now refuses them ---------------------------------------


@pytest.mark.unit
def test_the_project_route_validates_the_figma_default_file():
    import inspect

    from shared.routers import project_scoped

    src = inspect.getsource(project_scoped.upsert_project_credential)
    assert 'body.targetId == "figma"' in src
    assert "extract_file_key" in src
    assert "bad_figma_file" in src


@pytest.mark.unit
def test_the_refusal_names_the_field_and_what_it_wants():
    """A 422 saying only "invalid" would leave the user where they started — the
    original failure was already a true message pointing at the wrong place."""
    import inspect

    from shared.routers import project_scoped

    src = inspect.getsource(project_scoped.upsert_project_credential)
    flat = " ".join(src.split())
    assert "'Default file' must be a Figma file URL" in flat
    assert "not an account or email" in flat


@pytest.mark.unit
def test_validation_runs_before_the_secret_is_written():
    """Storing the token and then rejecting the row would leave a secret in the vault
    with no credential row pointing at it — the same ordering the route already takes
    care over for a failed vault write."""
    import inspect

    from shared.routers import project_scoped

    src = inspect.getsource(project_scoped.upsert_project_credential)
    assert src.index("bad_figma_file") < src.index("put_secret")


@pytest.mark.unit
def test_only_figma_is_validated_this_way():
    """`account` means something different for every connector — an email for Jira, a
    workspace for Slack, owner/repo for GitHub. Applying a Figma rule to those would
    reject valid input."""
    import inspect

    from shared.routers import project_scoped

    src = inspect.getsource(project_scoped.upsert_project_credential)
    guard = src[src.index('body.targetId == "figma"'):]
    assert "extract_file_key" in guard.split("owner =")[0]


@pytest.mark.unit
def test_an_empty_default_file_is_still_allowed():
    """It is optional, and passing a file URL per request is the better default for a
    project with more than one design file."""
    import inspect

    from shared.routers import project_scoped

    src = inspect.getsource(project_scoped.upsert_project_credential)
    assert '(body.account or "").strip()' in src


# -- the two doors onto this setting now agree --------------------------------


@pytest.mark.unit
def test_the_tenant_route_rejects_the_same_input():
    """The behaviour the project route was missing, asserted so the pair stays in
    step — if one is ever relaxed, this says the other must be too."""
    import inspect

    from shared.routers import connectors

    src = inspect.getsource(connectors._store_figma_credentials)
    assert "extract_file_key" in src
    assert "422" in src or "status_code=422" in src
