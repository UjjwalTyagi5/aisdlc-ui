# Deployment Agent — plan

Finishing the agent that ships the code: generate the deployment package, create the
pipeline when the project has none, run it under an approval gate, and report what
happened.

Not a new agent. `agents/deployer.py` and its seven tools already exist and work. This
plan closes the gap between what it does (write files into a PR) and what it was asked
to do (put a pipeline in place and run it, with governance).

---

## What already works

`/sdlc/agent/deployment` → `agents/deployer.py`, seven tools:

| Tool | Does |
|---|---|
| `inspect_repo` | detected stack, existing deploy assets, bound connector |
| `read_repo_file`, `search_repo` | codebase context |
| `read_upstream_artifacts` | testing + security gate evidence |
| `stage_deploy_file` | stage one generated file |
| `open_deploy_pr` | push a branch and open the PR |
| `submit_release` | structured readiness / risk / compliance assessment |

UI at `projects/[id]/deployment`: Readiness, Artifacts, Runbooks, Compliance.

A second route, `/sdlc/agent/deployment_orchestrator` → `agents/pipeline_app.py`
(2,338 lines), is marked legacy in `process_api.py`. It is not the agent being extended
here, and phase 6 decides its fate rather than leaving two live deployment paths.

## What is missing

1. **No Azure Pipelines connector.** `azure_devops.py` is work items and wiki. Nothing
   can list, create, or run an ADO pipeline. This is the largest single gap.
2. **The agent cannot deploy.** The prompt says so outright: *"never deploy to a live
   environment and never trigger a pipeline in v1"*.
3. **No Jenkinsfile** in the generated package.
4. **No repo sync.** The working copy is cloned once; nothing refreshes it.
5. **No gate.** `artifact:approve_deployment` exists in the permission catalog and is
   wired to nothing.
6. **`deployment` is absent from `BUILT_AGENTS`**, so it does not present as a real
   stage in the pipeline UI.

## Decisions taken

- **Pipeline-first, direct apply as an escape hatch.** The agent creates and triggers
  the pipeline; the pipeline deploys. Cluster credentials stay in ADO service
  connections or GitHub environments, where the org already governs them. Direct
  `kubectl` apply exists for projects with no CI and is gated harder — it makes the
  platform the audit boundary, which is the thing worth avoiding by default.
- **Jenkins: generate the file, do not drive the server.** A Jenkinsfile in the PR, the
  same as `azure-pipelines.yml`. No Jenkins connector, no per-tenant Jenkins token.
- **Reuse the artifact approval gate.** A deploy is a pending action a project admin
  approves, on the machinery already built and tested for artifacts — same audit trail,
  same permission catalog, same UI pattern.

## The governance rule this is built around

**NOTHING REACHES AN ENVIRONMENT WITHOUT A NAMED HUMAN APPROVING IT.** Generating files
is free and needs no gate. Creating a pipeline, triggering a run, and applying to a
cluster each change something outside the platform, and each requires
`artifact:approve_deployment` from someone who is not the run's initiator.

The failure mode to design against is the one this codebase keeps producing: an action
that fails safely, silently, behind a plausible message. A deploy that reports success
having done nothing is worse than one that errors.

---

## Phases

### Phase 0 — Azure Pipelines connector
New `config/connectors/azure_pipelines.py`, following `azure_repos.py` (separate from
`azure_devops.py`, matching the established split).

- read: `list_pipelines`, `get_pipeline`, `list_runs`, `get_run`
- write: `create_pipeline`, `run_pipeline`
- listen: `pipeline_run` webhook

Honest capability declarations. Where the ADO API cannot do a thing, it is declared
`not_supported` with the reason, as Jira's `team_capacity` is.

### Phase 1 — The deployment record and its gate
- `deployments` table: project, environment, target kind, pipeline ref, run ref,
  approval status, who approved, what was approved, outcome.
- Migration `0043_deployments`.
- Wire `artifact:approve_deployment`; approve/reject routes mirroring the artifact ones.
- Self-approval refused, matching `test_gate_self_approval.py`.

### Phase 2 — Complete the generated package
Jenkinsfile, Helm chart, `docker-compose.yml` where the stack warrants it. Prompt rules
so the agent picks the right set for the detected stack and connector rather than
emitting all of them.

### Phase 2b — Quality and vulnerability scanning in the generated pipeline
The pipeline the agent writes must scan the code, not just ship it.

**SonarQube.** The connector is already fully implemented (`create_project`,
`get_quality_gate_status`, `get_measures`, `list_issues`) and the deployment agent does
not touch it. Wire it up:
- generate the `SonarQubePrepare` / `SonarQubeAnalyze` / `SonarQubePublish` task triple
  into `azure-pipelines.yml`, the equivalent action into GH Actions, and the
  `withSonarQubeEnv` stage into the Jenkinsfile
- create the Sonar project when the repo has none, rather than emitting a pipeline that
  fails on first run against a project key that does not exist
- read the quality gate back as **gate evidence** and fold it into the release decision

**ADO-native scanning**, added to the generated pipeline when the stack warrants it:
- Microsoft Security DevOps (MSDO) — the umbrella ADO task covering credential scanning,
  IaC scanning (Terrascan/Checkov) and container scanning
- CodeQL, where the tenant has GitHub Advanced Security for Azure DevOps
- dependency scanning (`dotnet list package --vulnerable`, `npm audit`, `pip-audit`,
  `mvn dependency-check`) chosen by the detected stack
- container image scanning (Trivy) when the package includes a Dockerfile

**The gate rule.** A failing quality gate or a critical vulnerability makes the release
decision `no_go`, and the reason is named. The existing prompt already defaults to
`no_go` on unresolved critical security findings; this phase gives it real evidence to
read instead of "no upstream gate evidence".

**What it must not do.** Not every project has Sonar, GHAS, or a licence for the ADO
task. A scan stage that cannot run is declared missing, not quietly dropped and not
emitted anyway to fail on first run. `not_configured` is an answer.

### Phase 3 — Pipeline lifecycle tools
`list_pipelines`, `create_pipeline`, `run_pipeline`, `get_run_status` on the agent.
`create_pipeline` and `run_pipeline` are gated: they create a pending deployment record
and return "awaiting approval", never the thing itself.

### Phase 4 — Execution and reporting
Approval releases the action. Run status polled and streamed to the UI. A run that fails
says so with the failing stage — never a summary that reads like success.

### Phase 5 — Repo sync
`sync_repo` to refresh the working copy, and honest reporting when the branch has moved
under a staged package.

### Phase 6 — UI
Deployment workbench: target and environment selection, the generated package, a pending
approval banner with approve/reject, pipeline runs with live status, and history.
`deployment` added to `BUILT_AGENTS`. Decide the legacy `deployment_orchestrator` route.

### Phase 7 — Azure resource connector (design only)
Scaffolding for provisioning: what a resource plan looks like, how it is costed, and
where the approval sits. Deliberately not built here — flagged as future in the request,
and provisioning without the governance model above settled would be the wrong order.

---

## Reference points

- **Development agent** — `tools/git_tools.py` for ADO and GitHub repo operations. Reuse
  `shared/services/ado_repos.py`; do not duplicate clone/push/PR logic.
- **Testing agent** — `Nodes/approval.py` for a gate inside a graph, and its run-status
  aggregation for polling an external system.
- **PM agent** — the phasing, and the rule that arithmetic and external facts belong in
  deterministic code, not in the model's prose.
