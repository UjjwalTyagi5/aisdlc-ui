# Project Manager Agent — plan

An agent between Design and Development that does the planning work: turns an approved
design into a schedule, staffs it, tracks it, and tells a manager when it is slipping.

> The six scopes in the attached image did not come through. Everything below is derived
> from the codebase and from "timelines / resource planner". Send the image and this gets
> mapped onto your six.

---

## 1. Where it sits

`pipeline_position` is an integer and `_PORTFOLIO_1` is an ordered list, so inserting is
a renumber, not an append:

```
requirements 1 → design 2 → PM 3 → development 4 → code_review 5
→ security 6 → testing 7 → deployment 8 → documentation 9
```

`AgentDefinition` for it:

| Field | Value | Why |
|---|---|---|
| `input_artifacts` | `["requirements_payload", "design_artifacts"]` | It plans work described by requirements against a design that says how big it is |
| `output_artifact` | `plan_artifacts` | New Run column — see §5 |
| `gate_type` | `approval_required` | A plan that commits people and dates is a decision, not an output |
| `sla_hours` | `24` | Between Design's 48 and Development's |
| `can_parallel_with` | `[]` | Development reads its output |

**Development should read it.** Adding `plan_artifacts` to Development's
`input_artifacts` is what makes this agent worth having — otherwise it produces a plan
nothing consumes.

---

## 2. Scope

### Proposed six

1. **Work breakdown** — turn the design's components into estimable tasks, linked back to
   the stories that motivated them. Reads `design_artifacts` + `requirements_payload`;
   writes tasks to the board with parent links (the linking already exists).
2. **Estimation** — story points or hours per item, with the reasoning recorded. Flags
   items too large to estimate honestly and proposes splits.
3. **Schedule / timeline** — sequence the work into sprints or a dated plan, respecting
   dependencies. Produces the Gantt-style view and a critical path.
4. **Resource planning** — who does what, against real capacity: team roster, allocation
   percentages, leave. Surfaces over-allocation rather than silently overcommitting.
5. **Risk and dependency tracking** — blockers, cross-team dependencies, and what each
   one threatens. Feeds the risk register the Requirements agent already generates.
6. **Status reporting** — burndown, velocity, "are we on track", and a manager-readable
   summary on request or on a schedule.

### Worth adding

7. **Re-planning on change** — the honest one. Requirements change and designs get
   revised; a plan that cannot absorb that is a document, not a tool. Given a changed
   story, say what moves and by how much.
8. **Budget vs. plan** — the platform already tracks per-project spend and a budget cap
   (`monthly_budget_usd`, the Cost page). A PM agent that plans effort without looking at
   the money is only doing half the job, and the data is already there.

---

## 3. Connectors

### What exists and fits

| Connector | Used for |
|---|---|
| **Jira** / **Azure DevOps** | The plan IS the board. Sprints, assignments, estimates, states — read and write |
| **MS Teams** / **Slack** | Status to a channel; escalation when a date slips |
| **Confluence** / **SharePoint** | The plan document, status reports, RAID log |
| **MS Graph** | Calendars and out-of-office — the only real source of who is actually available |

### What is missing, and this is the important part

**The board connectors expose no planning data at all.** `make_board_item` — the
canonical shape both providers normalise into — carries `title`, `state`, `assigned_to`,
`tags`, `parent_id` and nothing else. There is:

- no `estimate` / `story_points`
- no `iteration` / `sprint` (ADO returns `iteration_path` in the raw row; it is dropped
  on the way through)
- no `start_date` / `due_date`
- no `remaining_work` / `completed_work`

And neither connector implements a single sprint, board or capacity adapter:

```
list_sprints / list_iterations / team_capacity / list_boards
  jira.py           0
  azure_devops.py   0
```

**So scopes 3, 4 and 6 cannot be built on today's connectors.** They need
connector work first, and that work is the real cost of this agent — not the agent.

### Connector work required

1. **Extend `make_board_item`** with `estimate`, `iteration`, `start_date`, `due_date`,
   `remaining_work`. Additive and low-risk: both providers already fetch the raw rows
   these come from. ADO's `iteration_path` is being discarded right now.
2. **Add sprint/iteration adapters** — `list_sprints`, `sprint_items`, `create_sprint`,
   `move_item_to_sprint`. Jira: Agile API `/rest/agile/1.0/board/{id}/sprint`. ADO:
   `/_apis/work/teamsettings/iterations`.
3. **Add capacity adapters** — `team_capacity`, `team_members`. ADO has a first-class
   capacity API. **Jira does not** — capacity there means Tempo or a similar plugin, so
   for Jira this is either MS Graph calendars or a manual roster. Worth deciding early
   rather than discovering during scope 4.
4. **Declare all of it in `CapabilityEntry`** so an unsupported provider fails closed
   with a clear message, as `list_teams` already does for Jira.

---

## 4. Permissions and ownership

`scrum_master` is currently `"use"` on every agent and owns none. It is the obvious
owner here, and this is the first agent where that role does real work.

```python
AGENT_DEFAULT_REACH["plan"] = {
    "project_admin": "owner",     # universal fallback approver
    "scrum_master": "owner",      # the role this agent exists for
    "ba": "use", "architect": "use", "developer": "use",
    "qa": "use", "security_engineer": "none",
    "devops_engineer": "none", "data_engineer": "use",
}
```

Also needed: `artifact:approve_plan` in the permission catalogue and a migration granting
it — same shape as `0039_artifact_delete_permission`.

**Writing to the board is Consequential.** Creating sprints, assigning people and moving
dates change a system outside the platform. Board writes already route through
`_board_connector("write")`, which is where the tier check lives, so this is inherited
rather than rebuilt — but it means assignment and scheduling need an owner's approval,
which is the correct behaviour and should be designed for, not discovered.

---

## 5. Data model

1. **Migration**: `runs.plan_artifacts` (JSONB), plus `"plan": "plan_artifacts"` in
   `_COLUMN_MAP` and `_JSONB_COLUMNS`. The Activity timeline picks it up automatically.
2. **`PlanArtifact` model** in `shared/models/artifacts.py`, alongside
   `RequirementsArtifact` and `DesignArtifact`. Shape: `tasks`, `schedule`,
   `assignments`, `risks`, `milestones`, `baseline`.
3. **A baseline field is worth having from day one.** Scope 7 needs "what did we commit
   to originally" to answer "how far have we slipped", and retrofitting a baseline after
   plans exist means the first N plans have no history.

---

## 6. Suggested order

| Phase | What | Blocked on |
|---|---|---|
| 0 | Connector extensions — item fields, sprints, capacity | Nothing. **Start here** |
| 1 | Agent skeleton: registry entry, `plan_artifacts` column, permission, route, empty chat | Nothing (parallel with 0) |
| 2 | Scopes 1–2 (breakdown, estimation) | Phase 1 |
| 3 | Scopes 3–4 (timeline, resources) | Phase 0 |
| 4 | Scopes 5–6 (risk, reporting) | Phase 3 |
| 5 | Scopes 7–8 (re-planning, budget) | Phase 4 |

Phase 0 first because 3 and 4 are the scopes people actually want, and they are the ones
today's connectors cannot serve.

---

## 7. Two things worth deciding before building

**Does the plan live in the platform or on the board?** If the board is the source of
truth — as it is for requirements today, where stories are synthesised from
`requirements_payload` and are read-only in the UI — then the PM agent writes to Jira/ADO
and the platform renders a view. If the platform owns the plan, you need write-back,
conflict handling, and an answer for a sprint someone changed in Jira. The requirements
side already chose "board is the source of truth"; consistency argues for the same here.

**Does it read the board live, or a snapshot?** Timelines are only useful if current, but
every stage today reads a stored payload rather than the live board. A PM agent whose
burndown is a day stale will be mistrusted; one that calls the board on every question
will be slow and rate-limited. A short-TTL cache in the connector layer is probably the
answer, but it is a decision, not a detail.
