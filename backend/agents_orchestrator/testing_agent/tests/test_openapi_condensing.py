"""A served OpenAPI document is the only unbounded input in the skill prompt — it
comes from whatever the target happens to be. Passed through verbatim, a real
application's spec (this platform's own is ~336k chars) becomes ~84k tokens in a
single call and exceeds a modest deployment's per-minute token budget on its own,
so API testing died on a provider rate-limit error and generated nothing.
"""
import json

from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import (
    _OPENAPI_INLINE_LIMIT,
    _condense_openapi_spec,
)


def _big_spec(n_paths: int) -> str:
    schema = {"type": "object", "properties": {f"f{i}": {"type": "string"} for i in range(40)}}
    return json.dumps({
        "openapi": "3.1.0",
        "info": {"title": "Big", "version": "1"},
        "servers": [{"url": "http://localhost:8004"}],
        "paths": {
            f"/thing/{i}": {
                "get": {
                    "summary": f"Get thing {i}",
                    "parameters": [{"name": "verbose", "in": "query"}],
                    "responses": {"200": {"content": {"application/json": {"schema": schema}}},
                                  "404": {}},
                },
                "post": {"summary": f"Make thing {i}", "requestBody": {"content": {"a": schema}},
                         "responses": {"201": {}}},
            }
            for i in range(n_paths)
        },
    })


def test_small_spec_passes_through_untouched():
    small = json.dumps({"openapi": "3.1.0", "paths": {"/health": {"get": {"responses": {"200": {}}}}}})
    assert _condense_openapi_spec(small) == small


def test_large_spec_is_condensed_below_the_limit():
    spec = _big_spec(300)
    assert len(spec) > _OPENAPI_INLINE_LIMIT

    out = _condense_openapi_spec(spec)

    assert len(out) < len(spec)


def test_condensing_keeps_every_endpoint_and_what_a_test_needs():
    out = json.loads(_condense_openapi_spec(_big_spec(300)))

    assert out["endpoint_count"] == 600  # a GET and a POST per path
    first = out["endpoints"][0]
    assert first["method"] in {"GET", "POST"}
    assert first["path"].startswith("/thing/")
    assert first["summary"]
    assert first["responses"]
    assert out["servers"] == [{"url": "http://localhost:8004"}]


def test_condensing_drops_the_schema_bodies_that_make_specs_huge():
    out = _condense_openapi_spec(_big_spec(300))

    # The 40-property schema repeated per operation is the bulk of the document.
    assert "properties" not in out


def test_an_unparseable_oversized_spec_is_dropped_rather_than_truncated():
    """Half a JSON document is worse than none — it cannot be parsed by the model
    and wastes the same tokens."""
    assert _condense_openapi_spec("x" * (_OPENAPI_INLINE_LIMIT + 10)) == "{}"
