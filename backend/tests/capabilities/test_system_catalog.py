from shared.capabilities import system_catalog as sc
from shared.capabilities import taxonomy


def test_all_catalog_capabilities_are_valid():
    taxonomy.assert_valid([t.capability for t in sc.SYSTEM_CATALOG])


def test_catalog_keys_unique():
    keys = [t.key for t in sc.SYSTEM_CATALOG]
    assert len(keys) == len(set(keys))


def test_requirements_gets_nlp_linter_default_on():
    caps = {t.capability for t in sc.curated_for_agent("requirements")}
    assert "req.quality.analyze" in caps


def test_disabled_curated_tool_is_excluded():
    enabled = sc.curated_for_agent("requirements")
    disabled = sc.curated_for_agent("requirements", disabled={enabled[0].key})
    assert all(t.key != enabled[0].key for t in disabled)
