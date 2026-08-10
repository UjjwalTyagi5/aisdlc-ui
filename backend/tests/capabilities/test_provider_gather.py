from shared.capabilities.providers import gather_native, gather_curated, gather_byo


class _Tool:
    def __init__(self, name):
        self.name = name


def test_gather_native_matches_tool_names_to_tags():
    tools = [_Tool("upload_file"), _Tool("generate_user_stories"), _Tool("unmapped_tool")]
    provs = gather_native("requirements", tools)
    caps = {p.capability for p in provs}
    assert "req.ingest" in caps          # upload_file
    assert "story.generate" in caps      # generate_user_stories
    # unmapped tool contributes no provider
    assert all(p.ref != "unmapped_tool" for p in provs)
    # native providers carry the bindable tool object
    assert all(p.tool is not None for p in provs)


def test_gather_curated_returns_one_provider_per_default_on_tool():
    provs = gather_curated("requirements", disabled=set(), curated_tools_by_key={})
    caps = {p.capability for p in provs}
    assert "req.quality.analyze" in caps
    # no bound managed-server tool available in this unit test → tool is None
    assert all(p.tier == "curated" for p in provs)


def test_gather_byo_reads_id_and_capabilities():
    server_rows = [{"id": "srv-1", "capabilities": ["quality.sca.scan", "quality.sast.scan"]}]
    provs = gather_byo(server_rows, mcp_tools=[])
    caps = {p.capability for p in provs}
    assert caps == {"quality.sca.scan", "quality.sast.scan"}
    assert all(p.tier == "byo" for p in provs)
    assert all(p.ref == "srv-1" for p in provs)
