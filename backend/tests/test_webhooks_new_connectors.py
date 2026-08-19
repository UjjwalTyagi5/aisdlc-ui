"""Inbound webhook tests for the github_actions and Microsoft Graph edges.

The central hazard these guard: a CI event must never start an SDLCWorkflow.
WebhookConsumer._create_run_from_webhook_event launches a full
requirements→design→development→testing→deployment pipeline per event, and GitHub
Actions emits an event on every queue/start/completion. Three independent barriers are
asserted here — a shape guard, a separate stream, and a consumer with no
start_workflow call.
"""
import ast
import hashlib
import hmac
import inspect
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webhooks.router import webhooks_router  # noqa: E402

_TENANT = "00000000-0000-0000-0000-000000000001"
_GHA_SECRET = "gha-webhook-secret-value"
_CLIENT_STATE = "c" * 40


class _FakeRedis:
    """Records XADDs and answers dedup from an in-memory set."""

    def __init__(self):
        self.streams = {}
        self.seen = set()

    async def xadd(self, key, fields, **kwargs):
        self.streams.setdefault(key, []).append(fields)
        return b"1-1"

    async def set(self, key, value, nx=False, ex=None):
        if key in self.seen:
            return None
        self.seen.add(key)
        return True

    async def publish(self, *_a, **_kw):
        return 1


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(webhooks_router)
    app.state.gha_webhook_secret = _GHA_SECRET
    app.state.msgraph_client_state = _CLIENT_STATE
    app.state.redis_pool = _FakeRedis()
    return TestClient(app), app


def _workflow_run_body(run_id=42):
    return json.dumps(
        {
            "action": "completed",
            "workflow_run": {
                "id": run_id,
                "name": "deploy",
                "status": "completed",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "abc123",
                "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
                "run_attempt": 1,
            },
            "repository": {"full_name": "o/r"},
        }
    ).encode()


def _signed(body: bytes, delivery: str, secret: str = _GHA_SECRET):
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Delivery": delivery,
        "Content-Type": "application/json",
    }


# ── GitHub Actions ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_workflow_run_lands_on_its_own_stream(client):
    """Events go to webhooks:github_actions — NOT webhooks:github, which the
    SDLCWorkflow-starting consumer subscribes to."""
    c, app = client
    body = _workflow_run_body()
    r = c.post(f"/webhooks/github_actions/{_TENANT}", content=body, headers=_signed(body, "d-1"))
    assert r.status_code == 200
    assert r.json() == {"status": "accepted", "event_id": "d-1"}

    streams = app.state.redis_pool.streams
    assert "webhooks:github_actions" in streams
    assert "webhooks:github" not in streams


@pytest.mark.unit
def test_replayed_delivery_is_deduplicated(client):
    c, app = client
    body = _workflow_run_body()
    headers = _signed(body, "d-dup")
    first = c.post(f"/webhooks/github_actions/{_TENANT}", content=body, headers=headers)
    second = c.post(f"/webhooks/github_actions/{_TENANT}", content=body, headers=headers)
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert len(app.state.redis_pool.streams["webhooks:github_actions"]) == 1


@pytest.mark.unit
def test_non_workflow_run_payload_is_rejected(client):
    """Barrier 1: a push/issues payload misdelivered here never enters the stream."""
    c, app = client
    body = json.dumps({"ref": "refs/heads/main", "commits": []}).encode()
    r = c.post(f"/webhooks/github_actions/{_TENANT}", content=body, headers=_signed(body, "d-2"))
    assert r.status_code == 400
    assert "webhooks:github_actions" not in app.state.redis_pool.streams


@pytest.mark.unit
def test_bad_signature_returns_400_never_401(client):
    """Auth failures must not leak auth semantics (T-m6-06-ID)."""
    c, _ = client
    body = _workflow_run_body()
    headers = _signed(body, "d-3", secret="wrong-secret")
    r = c.post(f"/webhooks/github_actions/{_TENANT}", content=body, headers=headers)
    assert r.status_code == 400
    assert r.json() == {"detail": "Bad Request"}


@pytest.mark.unit
def test_webhook_reachable_without_authorization_header(client):
    """Signature IS the auth — the JWT middleware bypasses the /webhooks/ prefix."""
    c, _ = client
    body = _workflow_run_body()
    r = c.post(f"/webhooks/github_actions/{_TENANT}", content=body, headers=_signed(body, "d-4"))
    assert r.status_code == 200


@pytest.mark.unit
def test_pipeline_consumer_makes_no_start_workflow_call():
    """Barrier 3, checked structurally.

    An AST check rather than a substring search: the module docstring names
    start_workflow deliberately, to explain why it must never appear as a CALL.
    """
    import webhooks.pipeline_consumer as pc

    def called_names(mod):
        names = set()
        for node in ast.walk(ast.parse(inspect.getsource(mod))):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute):
                    names.add(fn.attr)
                elif isinstance(fn, ast.Name):
                    names.add(fn.id)
        return names

    assert "start_workflow" not in called_names(pc)

    # The sanity half of this test compared against `webhooks.consumer`, which DOES
    # start workflows — except it does not exist any more. It was deleted by
    # 371a42f "Remove Temporal from the backend", and `start_workflow` went with
    # Temporal, so there is no longer a module anywhere that makes such a call for
    # this one to be contrasted against.
    #
    # The remaining assertion still earns its place: it is a standing guard that the
    # pipeline consumer never REGAINS a workflow start, which is exactly what
    # pipeline_consumer.py's own docstring says must never happen. What is gone is
    # only the comparison that proved it was not passing vacuously — and the
    # vacuity it guarded against is now the permanent state of the codebase.


# ── Microsoft Graph ───────────────────────────────────────────────────────────


def _notification(item_id="i1", client_state=_CLIENT_STATE):
    return {
        "subscriptionId": "sub-1",
        "clientState": client_state,
        "changeType": "updated",
        "resource": f"drives/d1/items/{item_id}",
        "resourceData": {"id": item_id},
        "tenantId": "entra-dir-1",
    }


@pytest.mark.unit
def test_validation_handshake_wins_route_ordering(client):
    """The handshake must answer 200 text/plain with the raw token echoed.

    Registered alongside the generic /webhooks/{connector}/{tenant_id} route, which
    would otherwise match first and 400 as an unknown connector — making the
    subscription impossible to create.
    """
    c, _ = client
    r = c.post(f"/webhooks/msgraph/{_TENANT}?validationToken=hello-123")
    assert r.status_code == 200
    assert r.text == "hello-123"
    assert r.headers["content-type"].startswith("text/plain")


@pytest.mark.unit
def test_notification_batch_is_accepted_and_published(client):
    c, app = client
    r = c.post(
        f"/webhooks/msgraph/{_TENANT}",
        json={"value": [_notification("i1"), _notification("i2")]},
    )
    assert r.status_code == 200
    assert r.json()["accepted"] == 2
    assert len(app.state.redis_pool.streams["webhooks:sharepoint"]) == 2


@pytest.mark.unit
def test_wrong_client_state_rejects_the_whole_batch(client):
    """clientState is the only authentication Graph offers — one bad element poisons all."""
    c, app = client
    r = c.post(
        f"/webhooks/msgraph/{_TENANT}",
        json={"value": [_notification("i1"), _notification("i2", client_state="WRONG")]},
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "Bad Request"}
    assert "webhooks:sharepoint" not in app.state.redis_pool.streams


@pytest.mark.unit
def test_unconfigured_client_state_fails_closed():
    """With nothing to authenticate against, anyone could inject change events."""
    app = FastAPI()
    app.include_router(webhooks_router)
    app.state.msgraph_client_state = ""
    app.state.redis_pool = _FakeRedis()
    c = TestClient(app)
    r = c.post(f"/webhooks/msgraph/{_TENANT}", json={"value": [_notification()]})
    assert r.status_code == 400


@pytest.mark.unit
def test_malformed_graph_body_is_rejected(client):
    c, _ = client
    assert c.post(f"/webhooks/msgraph/{_TENANT}", json={"not_value": []}).status_code == 400
    assert c.post(f"/webhooks/msgraph/{_TENANT}", content=b"{oops").status_code == 400


@pytest.mark.unit
def test_graph_events_carry_identifiers_only(client):
    """Graph never sends content — a consumer must re-fetch through the connector."""
    c, app = client
    c.post(f"/webhooks/msgraph/{_TENANT}", json={"value": [_notification("item-9")]})
    entry = app.state.redis_pool.streams["webhooks:sharepoint"][0]
    raw = entry.get("event") or entry.get(b"event")
    if isinstance(raw, bytes):
        raw = raw.decode()
    event = json.loads(raw)
    assert event["provider"] == "sharepoint"
    assert event["item_id"] == "item-9"
    assert event["drive_id"] == "d1"
    assert event["tenant_id"] == _TENANT
    assert "content" not in event
