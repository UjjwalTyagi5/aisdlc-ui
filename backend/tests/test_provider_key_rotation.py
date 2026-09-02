"""Rotating a provider key must actually rotate it.

The UI has always said "Enter a new key to rotate" and the API client documented
`"" clears it`. The backend's UpdateProviderIn declared no api_key field, so Pydantic's
default extra="ignore" dropped it and update_provider never touched the secret store.
PATCH returned 200, the UI toasted "Provider updated", and the old key stayed live —
so an admin rotating a COMPROMISED key believed they had contained it and had not.

Covers: the schema accepts what the frontend sends, unknown fields now 422 instead of
vanishing, and the resolver's key cache is evicted so a revoked key dies immediately.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.routers.model import UpdateProviderIn
from shared.services import model_resolver as mr


class TestTheSchemaAcceptsWhatTheFrontendSends:
    """frontend/components/app/edit-provider-dialog.tsx sends all of these."""

    def test_api_key_is_accepted(self):
        body = UpdateProviderIn(display_name="Anthropic", api_key="sk-ant-new")
        assert body.api_key == "sk-ant-new"
        assert "api_key" in body.model_fields_set

    def test_empty_api_key_is_distinguishable_from_absent(self):
        """"" means CLEAR; absent means LEAVE ALONE. Both must survive parsing."""
        cleared = UpdateProviderIn(api_key="")
        assert cleared.api_key == ""
        assert "api_key" in cleared.model_fields_set

        untouched = UpdateProviderIn(display_name="Anthropic")
        assert "api_key" not in untouched.model_fields_set

    def test_the_other_dropped_fields_are_accepted(self):
        body = UpdateProviderIn(
            api_base="https://gateway.example.internal/v1",
            rpm_limit=60, tpm_limit=90_000, cost_limit_usd=25.0,
        )
        assert body.api_base == "https://gateway.example.internal/v1"
        assert body.rpm_limit == 60
        assert body.tpm_limit == 90_000
        assert body.cost_limit_usd == 25.0

    def test_camelCase_aliases_still_work(self):
        body = UpdateProviderIn(rpmLimit=10, tpmLimit=20, costLimitUsd=1.5)
        assert (body.rpm_limit, body.tpm_limit, body.cost_limit_usd) == (10, 20, 1.5)


def test_an_unknown_field_is_now_a_422_not_a_silent_drop():
    """The actual regression: drift must fail loudly.

    Under the old extra="ignore" this constructed cleanly and the field vanished —
    which is exactly how a rotation could report success while doing nothing.
    """
    with pytest.raises(ValidationError):
        UpdateProviderIn(display_name="Anthropic", totally_unknown_field="x")


class TestTheResolverCacheIsEvicted:
    """A rotated or revoked key must stop working immediately, not in 300s."""

    def setup_method(self):
        mr._KEY_CACHE.clear()

    def teardown_method(self):
        mr._KEY_CACHE.clear()

    def test_invalidate_removes_exactly_one_credential(self):
        import time
        future = time.monotonic() + 300
        mr._KEY_CACHE[("t1", "model-a")] = ("sk-old", future)
        mr._KEY_CACHE[("t1", "model-b")] = ("sk-other", future)

        mr.invalidate_key_cache("t1", "model-a")

        assert ("t1", "model-a") not in mr._KEY_CACHE
        assert ("t1", "model-b") in mr._KEY_CACHE, "unrelated credentials must survive"

    def test_invalidate_without_a_ref_clears_the_whole_tenant(self):
        import time
        future = time.monotonic() + 300
        mr._KEY_CACHE[("t1", "model-a")] = ("sk-a", future)
        mr._KEY_CACHE[("t1", "model-b")] = ("sk-b", future)
        mr._KEY_CACHE[("t2", "model-c")] = ("sk-c", future)

        mr.invalidate_key_cache("t1")

        assert not [k for k in mr._KEY_CACHE if k[0] == "t1"]
        assert ("t2", "model-c") in mr._KEY_CACHE, "other tenants must be untouched"

    def test_invalidating_an_absent_entry_is_harmless(self):
        mr.invalidate_key_cache("t1", "model-never-cached")

    def test_model_config_helper_tolerates_a_null_ref(self):
        """A keyless provider has no ref; eviction must be a no-op, not an error."""
        from shared.services.model_config import _invalidate_resolver_cache
        _invalidate_resolver_cache("t1", None)
