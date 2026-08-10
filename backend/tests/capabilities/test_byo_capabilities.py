import pytest
from shared.services.mcp_registry import validate_byo_capabilities


def test_valid_byo_tags_pass():
    validate_byo_capabilities(["quality.sca.scan", "quality.sast.scan"])  # no raise


def test_unknown_byo_tag_rejected():
    with pytest.raises(ValueError):
        validate_byo_capabilities(["not.a.real.cap"])


def test_native_only_byo_tag_hard_blocked():
    with pytest.raises(ValueError) as exc:
        validate_byo_capabilities(["repo.write"])
    assert "native-only" in str(exc.value).lower()
