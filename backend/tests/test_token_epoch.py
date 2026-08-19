"""A revocation takes effect now, not when the token happens to lapse.

Permissions are resolved once at login and baked into the JWT; every `require_permission`
reads that claim and nothing re-checked it. Removing a role therefore did nothing until
the holder's token expired — deleting an `org_admin` binding left the badge and the access
intact, observed live and logged as P1 in docs/rbac-auth-plan.md.

`token_epoch` records one integer per user: when their access last changed. A token
carries `iat`; if it predates that moment its permission set is known to be stale and the
request is refused with a 401.

These tests drive the module against a fake Redis so they run without one, and cover the
decisions that are easy to get backwards: the direction of the comparison, what happens
with no Redis at all, and tokens minted before `iat` existed.

See finding 6 in docs/rbac-audit-2026-08-17.md.
"""
import time

import pytest

from shared.authz import token_epoch


class _FakeRedis:
    """Enough of the client for this module: get, set(ex=), and a pipeline."""

    def __init__(self, *, broken: bool = False):
        self.store: dict[str, str] = {}
        self.broken = broken

    async def get(self, key):
        if self.broken:
            raise ConnectionError("redis is down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.broken:
            raise ConnectionError("redis is down")
        self.store[key] = str(value)

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self):
                self.ops = []

            def set(self, key, value, ex=None):
                self.ops.append((key, value))

            async def execute(self):
                if outer.broken:
                    raise ConnectionError("redis is down")
                for key, value in self.ops:
                    outer.store[key] = str(value)

        return _Pipe()


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()
    monkeypatch.setattr(token_epoch, "_redis", lambda: client)
    return client


@pytest.fixture
def no_redis(monkeypatch):
    monkeypatch.setattr(token_epoch, "_redis", lambda: None)


# ── the core comparison ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_token_minted_before_the_change_is_stale(fake_redis):
    issued = int(time.time()) - 60
    await token_epoch.bump_user_epoch("t1", "u1")

    assert await token_epoch.is_token_stale("t1", "u1", issued) is True


@pytest.mark.asyncio
async def test_a_token_minted_after_the_change_is_fine(fake_redis):
    """The direction matters, and getting it backwards would reject every NEW token —
    which presents as "nobody can sign in" rather than as a security bug."""
    await token_epoch.bump_user_epoch("t1", "u1")
    issued = int(time.time()) + 5

    assert await token_epoch.is_token_stale("t1", "u1", issued) is False


@pytest.mark.asyncio
async def test_a_token_minted_in_the_same_second_as_the_change_is_stale(fake_redis):
    """The granularity edge, and the reason `_now_epoch` rounds up.

    `iat` is whole seconds, so a token minted at any point in second N carries `iat = N`.
    If a revocation during that same second recorded N, `iat < epoch` would be `N < N` —
    false — and the token would survive. Mint-then-revoke inside one second is exactly
    what an admin correcting a mistake produces.
    """
    issued = int(time.time())
    await token_epoch.bump_user_epoch("t1", "u1")

    assert await token_epoch.is_token_stale("t1", "u1", issued) is True


@pytest.mark.asyncio
async def test_a_user_who_never_changed_is_never_stale(fake_redis):
    """No epoch recorded is the common case — most users, most of the time."""
    assert await token_epoch.is_token_stale("t1", "nobody", int(time.time()) - 999) is False


@pytest.mark.asyncio
async def test_the_epoch_is_per_user(fake_redis):
    """Bumping one person must not sign out the rest of the organisation."""
    issued = int(time.time()) - 60
    await token_epoch.bump_user_epoch("t1", "u1")

    assert await token_epoch.is_token_stale("t1", "u1", issued) is True
    assert await token_epoch.is_token_stale("t1", "u2", issued) is False


@pytest.mark.asyncio
async def test_the_epoch_is_per_tenant(fake_redis):
    issued = int(time.time()) - 60
    await token_epoch.bump_user_epoch("t1", "u1")

    assert await token_epoch.is_token_stale("t2", "u1", issued) is False


# ── the accommodations ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_token_without_iat_is_not_rejected(fake_redis):
    """Tokens minted before the claim existed pass unchecked, mirroring the JTI check.
    Rejecting them would sign out everyone on the deploy that added it, and they expire
    within the hour regardless."""
    await token_epoch.bump_user_epoch("t1", "u1")

    assert await token_epoch.is_token_stale("t1", "u1", None) is False
    assert await token_epoch.is_token_stale("t1", "u1", 0) is False


@pytest.mark.asyncio
async def test_without_redis_nothing_is_stale_and_nothing_raises(no_redis):
    """Local dev has no Redis. The check must degrade to the old behaviour rather than
    refusing every request or blowing up in the middleware."""
    await token_epoch.bump_user_epoch("t1", "u1")
    assert await token_epoch.bump_many("t1", ["u1", "u2"]) == 0
    assert await token_epoch.is_token_stale("t1", "u1", int(time.time()) - 999) is False


@pytest.mark.asyncio
async def test_a_redis_failure_fails_open_rather_than_locking_everyone_out(monkeypatch):
    """Fail-open is a deliberate choice, in line with the JTI denylist: fail-closed makes
    Redis a hard dependency of every authenticated request, so a blip takes the product
    down instead of briefly widening a revocation window that used to be an hour."""
    monkeypatch.setattr(token_epoch, "_redis", lambda: _FakeRedis(broken=True))

    await token_epoch.bump_user_epoch("t1", "u1")  # must not raise
    assert await token_epoch.bump_many("t1", ["u1"]) == 0
    assert await token_epoch.is_token_stale("t1", "u1", int(time.time()) - 999) is False


# ── bulk ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bump_many_marks_every_holder(fake_redis):
    """A role-permission retune affects everyone holding the role, not just the editor."""
    issued = int(time.time()) - 60
    marked = await token_epoch.bump_many("t1", ["a", "b", "c"])

    assert marked == 3
    for user in ("a", "b", "c"):
        assert await token_epoch.is_token_stale("t1", user, issued) is True


@pytest.mark.asyncio
async def test_bump_many_ignores_duplicates_and_blanks(fake_redis):
    assert await token_epoch.bump_many("t1", ["a", "a", "", "b"]) == 2
    assert await token_epoch.bump_many("t1", []) == 0
    assert await token_epoch.bump_many("", ["a"]) == 0
