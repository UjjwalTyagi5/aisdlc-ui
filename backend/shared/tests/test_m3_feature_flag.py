"""Unit tests for milestone-3 worker-pool feature-flag env vars.

Confirms the M3 env vars added to config/env.py are importable with the correct
types and that the reclaim timeout default meets the spec minimum.
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.mark.unit
def test_enable_worker_pool_importable():
    """The M3 worker-pool env vars import with correct types."""
    from config.env import (
        ENABLE_WORKER_POOL,
        WORKER_RECLAIM_TIMEOUT_MS,
    )

    assert isinstance(ENABLE_WORKER_POOL, bool)
    assert isinstance(WORKER_RECLAIM_TIMEOUT_MS, int)


@pytest.mark.unit
def test_worker_reclaim_timeout_default():
    """WORKER_RECLAIM_TIMEOUT_MS defaults to at least 60000ms (spec floor)."""
    from config.env import WORKER_RECLAIM_TIMEOUT_MS

    assert WORKER_RECLAIM_TIMEOUT_MS >= 60000
