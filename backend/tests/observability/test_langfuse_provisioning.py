"""Per-project Langfuse provisioning — the parts that must be right offline.

The interesting behaviour needs a live Langfuse database, so what is pinned here is
everything that can be checked without one: the key format, the hash scheme Langfuse
authenticates against, and the configuration guards that turn a misconfiguration into a
readable error instead of keys that silently 401.

THE HASH TEST IS THE IMPORTANT ONE. `fast_hashed_secret_key` is what Langfuse compares
on the hot path. Get it wrong and provisioning still "succeeds" — rows are written, keys
look plausible — and every trace is rejected with a 401 nobody sees, because tracing
fails open. An assertion here is the difference between a bug found in a test and a bug
found as missing data weeks later.
"""
from __future__ import annotations

import hashlib

import pytest

from shared.observability.provisioning import (
    LangfuseProvisioner,
    LangfuseProvisioningError,
    generate_key_pair,
    hash_secret_key,
)


def test_key_pair_has_the_shape_langfuse_issues():
    public_key, secret_key = generate_key_pair()
    assert public_key.startswith("pk-lf-")
    assert secret_key.startswith("sk-lf-")
    # Hex of token_hex(16)/token_hex(24): the lengths Langfuse's own generator produces.
    assert len(public_key) == len("pk-lf-") + 32
    assert len(secret_key) == len("sk-lf-") + 48


def test_key_pairs_are_unique():
    assert len({generate_key_pair()[1] for _ in range(50)}) == 50


def test_fast_hash_matches_langfuses_own_scheme():
    """SHA256(secret + SHA256_hex(salt)) — Langfuse's createShaHash(privateKey, salt).

    Reimplemented here from the definition rather than copied from the implementation,
    so this fails if the implementation drifts toward something that merely looks right.
    """
    secret = "sk-lf-" + "a" * 48
    salt = "salt"

    _bcrypt_hash, fast = hash_secret_key(secret, salt)

    expected = hashlib.sha256(
        (secret + hashlib.sha256(salt.encode()).hexdigest()).encode()
    ).hexdigest()
    assert fast == expected


def test_a_different_salt_yields_a_different_hash():
    """The failure mode this guards: a wrong salt authenticates against nothing."""
    secret = "sk-lf-" + "b" * 48
    assert hash_secret_key(secret, "salt")[1] != hash_secret_key(secret, "pepper")[1]


def test_bcrypt_hash_verifies_against_the_secret():
    import bcrypt

    secret = "sk-lf-" + "c" * 48
    hashed, _fast = hash_secret_key(secret, "salt")
    assert bcrypt.checkpw(secret.encode(), hashed.encode())
    assert not bcrypt.checkpw(b"sk-lf-wrong", hashed.encode())


@pytest.mark.asyncio
async def test_missing_db_url_is_a_readable_error():
    with pytest.raises(LangfuseProvisioningError) as ei:
        await LangfuseProvisioner(db_url="", salt="salt").provision(
            unit_name="unit", project_name="project"
        )
    assert "LANGFUSE_DB_URL" in str(ei.value)


@pytest.mark.asyncio
async def test_missing_salt_is_refused_rather_than_guessed():
    """Provisioning without the instance's salt would mint keys that always 401.

    Failing here is the whole point: the alternative is a project that looks provisioned
    and never receives a trace.
    """
    with pytest.raises(LangfuseProvisioningError) as ei:
        await LangfuseProvisioner(db_url="postgresql://x/y", salt="").provision(
            unit_name="unit", project_name="project"
        )
    assert "LANGFUSE_SALT" in str(ei.value)
