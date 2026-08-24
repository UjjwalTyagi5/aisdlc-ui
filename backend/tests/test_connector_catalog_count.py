"""The connector catalogue is narrower than the accept-set, and must stay so.

`_KNOWN_KINDS` is what the API accepts; `_CATALOG_KINDS` is what the product
presents as a tile. The org overview counted the former, so an Org Admin saw
"0 of 11 available" above an Integrations page rendering eight connectors.

These pin the distinction: the catalogue stays a subset of the accept-set (a
tile for a kind the API rejects would be a dead Connect button), the three kinds
with no tile stay out of it, and the grant surface offers exactly the catalogue
— granting a kind nobody can reach is a permission with nowhere to be used.
"""

import pytest

from shared.routers import connectors as conn
from shared.routers import integration_access as ia

# The tile grouping from frontend/app/(app)/integrations/page.tsx (CATEGORIES).
TILE_KINDS = {
    "jira",
    "azure_devops",
    "github",
    "github_actions",
    "slack",
    "ms_teams",
    "sharepoint",
    "figma",
    "confluence",
    "sonarqube",
}


@pytest.mark.unit
def test_catalog_matches_the_tiles_the_frontend_renders():
    assert conn._CATALOG_KINDS == TILE_KINDS


@pytest.mark.unit
def test_catalog_is_a_subset_of_the_accepted_kinds():
    assert conn._CATALOG_KINDS <= conn._KNOWN_KINDS


@pytest.mark.unit
def test_kinds_without_a_tile_are_excluded():
    # azure_repos keeps its webhook and normalizer plumbing, so it stays in
    # _KNOWN_KINDS — it just has no tile of its own any more.
    for kind in ("azure_repos", "sso_okta", "sso_entra"):
        assert kind in conn._KNOWN_KINDS
        assert kind not in conn._CATALOG_KINDS


@pytest.mark.unit
def test_grant_surface_knows_only_the_catalog():
    """The list read and the grant writes must agree on the grantable universe.

    They disagreed by construction while this module held both names: the
    listing enumerated one set and the writes validated against the other.
    """
    assert hasattr(ia, "_CATALOG_KINDS")
    assert not hasattr(ia, "_KNOWN_KINDS")
