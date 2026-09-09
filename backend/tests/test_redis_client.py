"""REDIS_URL is the only knob: TLS, auth, port and cluster mode all come from it.

Moving to a managed Redis should be a change to one environment variable and nothing
else. These assertions are what make that claim checkable — and they pin the bounded
timeouts, without which the platform's fail-open Redis paths take ~30 seconds each to
fail open, which is a hang rather than a degradation.
"""
from __future__ import annotations

import pytest

from shared.redis_client import (
    CONNECT_TIMEOUT_S,
    OPERATION_TIMEOUT_S,
    is_cluster_url,
    redis_from_url,
)

AZURE = "rediss://:the-access-key@amc-redis-d.centralindia.redis.azure.net:10000/0"


def _kwargs(client):
    return client.connection_pool.connection_kwargs


def test_plain_local_url_is_a_single_node_client():
    c = redis_from_url("redis://localhost:6379/0")
    assert type(c).__name__ == "Redis"
    assert _kwargs(c)["port"] == 6379


def test_timeouts_are_bounded_by_default():
    """The whole reason this factory exists — see the module docstring."""
    kw = _kwargs(redis_from_url("redis://localhost:6379/0"))
    assert kw["socket_connect_timeout"] == CONNECT_TIMEOUT_S
    assert kw["socket_timeout"] == OPERATION_TIMEOUT_S


def test_callers_can_still_override():
    kw = _kwargs(redis_from_url("redis://localhost:6379/0", socket_connect_timeout=0.25))
    assert kw["socket_connect_timeout"] == 0.25


def test_a_rediss_url_carries_tls_password_and_port():
    """Everything a managed Azure Redis needs, derived from the URL alone."""
    c = redis_from_url(AZURE)
    kw = _kwargs(c)
    assert c.connection_pool.connection_class.__name__ == "SSLConnection"
    assert kw["port"] == 10000
    assert kw["password"] == "the-access-key"
    assert kw["host"].endswith("redis.azure.net")


def test_cluster_mode_is_selected_by_the_url_itself():
    """The one thing a URL cannot imply is whether the server speaks cluster protocol.

    It travels as a query parameter so that the URL remains the only thing to change.
    """
    assert is_cluster_url(AZURE) is False
    assert is_cluster_url(AZURE + "?cluster=true") is True

    c = redis_from_url(AZURE + "?cluster=true")
    assert type(c).__name__ == "RedisCluster"


@pytest.mark.parametrize("flag", ["true", "TRUE", "1", "yes", "on"])
def test_cluster_flag_spellings(flag):
    assert is_cluster_url(f"{AZURE}?{'cluster'}={flag}") is True


@pytest.mark.parametrize("flag", ["false", "0", "no", ""])
def test_cluster_flag_off_spellings(flag):
    assert is_cluster_url(f"{AZURE}?cluster={flag}") is False


def test_cluster_parameter_is_stripped_before_redis_sees_it():
    """redis-py rejects unknown connection arguments, so the flag must not reach it."""
    c = redis_from_url(AZURE + "?cluster=true")
    # Constructing at all is the assertion: an unstripped `cluster` kwarg raises.
    assert type(c).__name__ == "RedisCluster"


def test_other_query_parameters_survive():
    c = redis_from_url("redis://localhost:6379/0?client_name=sdlc&cluster=false")
    assert _kwargs(c).get("client_name") == "sdlc"
