import json
import uuid

from shared.models.copilot import ChoiceCard, ChoiceOption


def _loads(raw) -> list:
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or [])
        return data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
    except Exception:
        return []


def project_choice_card(run_id: str, projects_json) -> ChoiceCard:
    # id = project NAME: the answer is echoed to the agent as text ("I select: <id>")
    # and the board tools identify a project by name, not by its GUID. Using the GUID
    # here left the agent unable to map the selection and it pulled the wrong project.
    # The GUID is preserved in meta for any downstream consumer that needs it.
    opts = [
        ChoiceOption(
            id=str(p.get("name") or p.get("id")),
            label=str(p.get("name") or p.get("id")),
            meta={"project_id": str(p.get("id"))} if p.get("id") else None,
        )
        for p in _loads(projects_json)
    ]
    return ChoiceCard(
        card_id=str(uuid.uuid4()),
        run_id=run_id,
        stage="requirements",
        kind="ado_project",
        prompt="Which Azure DevOps project should I pull from?",
        options=opts,
        min_select=1,
        max_select=1,
    )


def story_choice_card(run_id: str, stories_json) -> ChoiceCard:
    items = _loads(stories_json)
    opts = [
        ChoiceOption(
            id=str(s.get("id")),
            label=str(s.get("title") or s.get("id")),
            sublabel=s.get("state"),
        )
        for s in items
    ]
    return ChoiceCard(
        card_id=str(uuid.uuid4()),
        run_id=run_id,
        stage="requirements",
        kind="story_multiselect",
        prompt="Which stories should we take forward?",
        options=opts,
        min_select=1,
        max_select=max(1, len(opts)),
    )


def testing_type_card(run_id: str) -> ChoiceCard:
    return ChoiceCard(
        card_id=f"testing-type-{run_id}",
        run_id=run_id,
        stage="testing",
        kind="custom",
        prompt="What kind of testing should I run?",
        options=[
            ChoiceOption(id="unit", label="Unit tests",
                         sublabel="Generate + run tests against the code, with coverage"),
            ChoiceOption(id="functional", label="Functional (browser)",
                         sublabel="Drive a real browser against a running app — I'll ask for the URL"),
            ChoiceOption(id="api", label="API tests",
                         sublabel="Validate endpoints / contracts against the design"),
        ],
        min_select=1,
        max_select=3,
    )


def testing_url_card(run_id: str, scope_label: str) -> ChoiceCard:
    kind_word = "application" if scope_label == "functional" else "API base"
    return ChoiceCard(
        card_id=f"testing-url-{run_id}",
        run_id=run_id,
        stage="testing",
        kind="custom",
        prompt=f"Paste the {kind_word} URL to test",
        options=[],   # free-text only — the card's free-text Input collects the URL
        min_select=0,
        max_select=0,
    )
