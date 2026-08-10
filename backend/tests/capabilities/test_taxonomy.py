import pytest
from shared.capabilities import taxonomy


def test_known_capability_is_valid():
    assert taxonomy.is_valid("repo.write")
    assert taxonomy.is_valid("req.ingest")
    assert taxonomy.is_valid("artifact.write")


def test_unknown_capability_is_invalid():
    assert not taxonomy.is_valid("totally.made.up")


def test_assert_valid_raises_listing_unknown():
    with pytest.raises(ValueError) as exc:
        taxonomy.assert_valid(["repo.write", "totally.made.up", "also.bad"])
    msg = str(exc.value)
    assert "totally.made.up" in msg and "also.bad" in msg
    assert "repo.write" not in msg  # only the unknown ones are listed


def test_native_only_subset_is_within_vocab():
    assert taxonomy.NATIVE_ONLY <= taxonomy.CAPABILITIES


def test_repo_write_is_native_only():
    assert taxonomy.is_native_only("repo.write")
    assert not taxonomy.is_native_only("quality.sast.scan")
