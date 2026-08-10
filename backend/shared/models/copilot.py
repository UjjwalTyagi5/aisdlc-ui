from typing import Literal, Optional
from pydantic import BaseModel, Field


class ChoiceOption(BaseModel):
    id: str
    label: str
    sublabel: Optional[str] = None
    meta: Optional[dict] = None


class ChoiceCard(BaseModel):
    card_id: str
    run_id: str
    stage: str
    kind: Literal["ado_project", "story_multiselect", "repo", "branch", "confirm", "custom"]
    prompt: str
    options: list[ChoiceOption] = Field(default_factory=list)
    min_select: int = 1
    max_select: int = 1


class ChoiceAnswer(BaseModel):
    card_id: str
    selected_ids: list[str] = Field(default_factory=list)
    free_text: Optional[str] = None
