from shared.models.orm import AgentProfile, _RLS_TABLES


def test_agent_profile_model_exists_with_columns():
    cols = AgentProfile.__table__.columns.keys()
    for c in ["tenant_id", "agent_id", "scope", "scope_id", "version",
              "prompt_prepend", "prompt_append", "enabled_capabilities",
              "disabled_curated", "primary_overrides", "thresholds"]:
        assert c in cols


def test_agent_profiles_is_rls_table():
    assert "agent_profiles" in _RLS_TABLES
