"""Load/concurrency tests for the three real shared-resource constraints found while
verifying the Development agent (spec 5.3):
  1. Every project's pulled repo lives at one shared filesystem path
     (WORKSPACE_ROOT/tenant/project/repo) -- not per-session.
  2. Sandboxed command execution rejects shell-metacharacter injection independently
     per call -- no shared mutable state should let one session's rejection leak into
     another session's legitimate command.
  3. The Model Gateway's per-call cost cap must degrade as a legible, typed error
     under concurrent chat turns, not an unhandled exception.

Minimal async harness (asyncio + real async functions) -- no new framework
dependency, scoped to what these three constraints actually need proven."""
import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def shared_repo_workspace():
    """Simulates dev_workspace.py's one-checkout-per-project layout: a single
    real git repo two "concurrent sessions" both write into."""
    d = tempfile.mkdtemp(prefix="dev_agent_load_workspace_")
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, check=True)
    (Path(d) / "shared.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=d, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=d, check=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


async def test_concurrent_writes_to_the_shared_project_workspace_do_not_corrupt_the_repo(
    shared_repo_workspace,
):
    """Two "sessions" (developers) writing different files into the SAME project
    workspace concurrently must not corrupt the repo -- every write must land as a
    real, individually readable file, and `git status` must stay parseable
    afterward (not report a corrupted index).

    file_tools' write functions resolve the work dir from session state via
    _get_work_dir() -- for this harness we write directly against the shared path
    to isolate the filesystem behavior under test from session plumbing, matching
    how the underlying workspace_fs is actually exercised (many sessions, one path
    per project)."""

    async def _write_one(name: str, content: str):
        path = Path(shared_repo_workspace) / name
        await asyncio.to_thread(path.write_text, content, encoding="utf-8")

    await asyncio.gather(*[
        _write_one(f"concurrent_{i}.py", f"value = {i}\n") for i in range(20)
    ])

    for i in range(20):
        f = Path(shared_repo_workspace) / f"concurrent_{i}.py"
        assert f.read_text(encoding="utf-8") == f"value = {i}\n"

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=shared_repo_workspace, capture_output=True, text=True
    )
    assert status.returncode == 0
    assert len(status.stdout.strip().splitlines()) == 20


async def test_concurrent_sandbox_command_validations_stay_isolated():
    """sandbox_policy.validate_command must independently refuse a shell-operator
    injection attempt in one "session" while a clean, single command succeeds in
    another concurrently -- no shared mutable state should let one session's
    rejection affect another's legitimate call.

    Verified against the real implementation (agents_orchestrator/development_agent/
    tools/sandbox_policy.py): validate_command's actual contract is shell-operator /
    metacharacter rejection (&&, ||, ;, |, backtick, $(...), ${...}, newline) --
    it does NOT itself judge a command "dangerous" by name (e.g. plain `rm -rf /`
    passes it with no operators to catch; the SAFE_PREFIXES allowlist that would
    reject `rm` entirely lives separately in git_tools.run_command, per this
    module's own docstring: "allowlist prefix check lives in run_command itself").
    So the disallowed case here uses a command that actually trips the operator
    check -- chaining a destructive command onto an allowed one via `&&`, the
    injection shape validate_command exists to catch -- not a bare `rm -rf /`."""
    from agents_orchestrator.development_agent.tools.sandbox_policy import validate_command

    async def _check(cmd: str) -> str | None:
        return await asyncio.to_thread(validate_command, cmd)

    results = await asyncio.gather(*[
        _check("npm test && rm -rf /") if i % 2 == 0 else _check("ls -la") for i in range(40)
    ])

    disallowed = results[0::2]
    allowed = results[1::2]
    assert all(r is not None for r in disallowed), (
        "every 'npm test && rm -rf /' (shell-operator chaining) must be refused"
    )
    assert all(r is None for r in allowed), "every 'ls -la' must be allowed, unaffected by concurrent refusals"


async def test_model_gateway_cost_cap_degrades_legibly_under_concurrent_calls(monkeypatch):
    """guarded_completion must raise a legible, typed error (not hang, not an
    unhandled exception) when the per-call cost cap is exceeded, and must do so
    consistently across many concurrent calls against the same resolved model --
    proving the cap isn't a check that only works for a single caller at a time.

    Verified against the real implementation (shared/services/model_call_wrapper.py
    guarded_completion + shared/services/model_rate_limit.enforce_per_call_cost):
    the cap is a SYNCHRONOUS PRE-CALL estimate, and it only fires when BOTH
    resolved.max_cost_per_call_usd AND resolved.input_price_per_million are set --
    `if input_price:` guards the whole check, so a ResolvedModel missing
    input_price_per_million (as an earlier draft of this test had it) never even
    computes an estimate and the cap is silently skipped, letting ainvoke run.
    The brief's original ResolvedModel also passed messages=[] to guarded_completion;
    _messages_text([]) flattens to "" and estimate_input_tokens("") is 0, so even
    with input_price_per_million set the estimated cost would be $0 and never
    exceed the cap. Both are fixed here: input_price_per_million is set, and a
    long prompt is passed so the estimate is unambiguously over the tiny cap --
    mirroring tests/test_model_call_wrapper.py::test_per_call_cost_cap_blocks_before_any_network_call,
    the existing single-caller proof of this same contract. The raised type is
    shared.services.model_rate_limit.ModelCostLimitError specifically (not just
    "some Exception"), asserted by name below."""
    from shared.services import model_call_wrapper
    from shared.services.model_resolver import ResolvedModel

    resolved = ResolvedModel(
        provider="anthropic", litellm_provider="anthropic", model="claude-sonnet-4-6",
        api_key="fake-key", base_url=None, alias="load-test-alias",
        max_cost_per_call_usd=0.0001,  # deliberately tiny -- any real call trips it
        input_price_per_million=15.0,  # must be set: enforce_per_call_cost's caller
                                        # (guarded_completion) skips the whole cap
                                        # check when this is falsy
    )
    long_prompt = "x" * 100_000  # ensures the pre-call estimate is well over the cap

    class _ExpensiveModel:
        async def ainvoke(self, messages, **kwargs):
            # The cap is a pre-call estimate check -- guarded_completion must raise
            # ModelCostLimitError BEFORE ever calling ainvoke, so this must not run.
            raise AssertionError("ainvoke should not be reached once the cap trips pre-call")

    async def _one_call():
        with pytest.raises(Exception) as exc_info:
            await model_call_wrapper.guarded_completion(
                resolved, _ExpensiveModel(), long_prompt, tenant_id="load-test-tenant", agent_type="development",
            )
        return type(exc_info.value).__name__

    results = await asyncio.gather(*[_one_call() for _ in range(10)])
    # Every concurrent call must fail with the SAME typed error, not a mix of the
    # intended cap error and something incidental (a race in shared state) -- and
    # that type must be the real cap error, not the ainvoke-reached canary above.
    assert set(results) == {"ModelCostLimitError"}, (
        f"inconsistent or wrong failure type(s) under concurrency: {set(results)}"
    )
