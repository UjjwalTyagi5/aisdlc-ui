"""The rules of a tech stack, with no database: what is valid, which stack a project
follows, what the model is told, and how its Technology Stack table is checked."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.services.tech_stack import (  # noqa: E402
    NOT_COVERED, EffectiveTechStack, StackViolation, TechStack, annotate_stack_section,
    check_stack_table, correction_note, decide_effective, render_for_prompt, validate_stack,
)
from shared.services.tech_stack_catalog import CATEGORY_IDS, catalog  # noqa: E402

NODE = TechStack(id="s-node", scope="workspace", workspace_id="w1", project_id=None, name="Node + Next.js",
                 categories={"languages": ["TypeScript"], "backend_frameworks": ["Node.js", "Express"],
                             "frontend_frameworks": ["Next.js"], "databases": ["PostgreSQL"],
                             "cloud_hosting": ["Azure"]},
                 notes="Prefer managed services.", is_default=True)
JAVA = TechStack(id="s-java", scope="workspace", workspace_id="w1", project_id=None, name="Java + Spring Boot",
                 categories={"languages": ["Java"], "backend_frameworks": ["Spring Boot"]})
OWN = TechStack(id="s-own", scope="project", workspace_id=None, project_id="p1", name="QuickLink custom",
                categories={"languages": ["Go"]})

TABLE = """## TECHNOLOGY STACK

| Layer | Technology | Version | Justification |
|---|---|---|---|
| Frontend | **Next.js** | 14 | SSR |
| Backend | Node.js (Express) | 20 | team skills |
| Database | PostgreSQL | 16 | relational |
| Caching | Not covered by the project's tech stack — needs a decision | — | — |

## SECURITY DESIGN CHECKLIST
| Layer | Technology |
|---|---|
| Anything | MongoDB |
"""


def test_the_catalogue_names_every_category_in_order():
    assert CATEGORY_IDS[0] == "languages" and CATEGORY_IDS[-1] == "observability"
    cats = catalog()["categories"]
    assert [c["id"] for c in cats] == list(CATEGORY_IDS)
    assert "Spring Boot" in next(c for c in cats if c["id"] == "backend_frameworks")["suggestions"]


def test_a_valid_stack_is_trimmed_deduplicated_and_ordered_by_category():
    fields, violations = validate_stack(
        "  Node + Next.js ", "", {"databases": ["PostgreSQL"], "languages": [" TypeScript", "typescript", ""]}, "")
    assert violations == []
    assert fields["name"] == "Node + Next.js"
    assert list(fields["categories"]) == ["languages", "databases"]
    assert fields["categories"]["languages"] == ["TypeScript"]


def test_every_limit_is_named_on_its_field():
    _, v = validate_stack("ab", "d" * 281, {"nope": ["x"], "languages": ["x" * 61],
                                             "testing": [f"t{i}" for i in range(21)]}, "n" * 2001)
    codes = {(x["field"], x["code"]) for x in v}
    assert ("name", "name_length") in codes and ("description", "description_length") in codes
    assert ("notes", "notes_length") in codes and ("categories.nope", "unknown_category") in codes
    assert ("categories.languages", "item_length") in codes and ("categories.testing", "too_many_items") in codes


def test_an_empty_stack_is_refused():
    _, v = validate_stack("Empty stack", "", {"languages": ["  "]}, "")
    assert [x["code"] for x in v] == ["empty_stack"]


def test_the_projects_choice_wins_then_the_default_then_nothing():
    chosen = decide_effective(project_id="p1", workspace_id="w1", selected=JAVA, selection_id="s-java", default=NODE)
    assert chosen.stack is JAVA and chosen.source == "project_selection" and chosen.warning is None
    own = decide_effective(project_id="p1", workspace_id="w1", selected=OWN, selection_id="s-own", default=NODE)
    assert own.stack is OWN
    default = decide_effective(project_id="p1", workspace_id="w1", selected=None, selection_id=None, default=NODE)
    assert default.stack is NODE and default.source == "bu_default"
    none = decide_effective(project_id="p1", workspace_id="w1", selected=None, selection_id=None, default=None)
    assert none.stack is None and none.source == "none" and none.warning is None


def test_a_deleted_or_foreign_selection_falls_back_and_says_so():
    gone = decide_effective(project_id="p1", workspace_id="w1", selected=replace(JAVA, deleted=True),
                            selection_id="s-java", default=NODE)
    assert gone.stack is NODE and "was deleted" in gone.warning and "Node + Next.js" in gone.warning
    foreign = decide_effective(project_id="p1", workspace_id="w2", selected=JAVA, selection_id="s-java", default=None)
    assert foreign.stack is None and "another Business Unit" in foreign.warning
    assert "recommend a stack freely" in foreign.warning


def test_the_prompt_block_lists_the_stack_and_the_rules():
    block = render_for_prompt(EffectiveTechStack(NODE, "bu_default"))
    assert block.startswith("PROJECT TECH STACK — MANDATORY")
    assert '"Node + Next.js" (the Business Unit default)' in block
    assert "- Backend frameworks: Node.js, Express" in block and "Prefer managed services." in block
    assert NOT_COVERED in block
    assert "Versions are not part of the list" in block     # the marker once landed in the Version column
    assert render_for_prompt(None) == "" and render_for_prompt(EffectiveTechStack(None, "none")) == ""


def test_the_table_check_reads_only_the_technology_stack_section():
    eff = EffectiveTechStack(NODE, "bu_default")
    assert check_stack_table(eff, TABLE) == []          # MongoDB is in another section; not-covered passes
    bad = TABLE.replace("| PostgreSQL | 16 |", "| MongoDB | 7 |")
    assert check_stack_table(eff, bad) == [StackViolation(layer="Database", technology="MongoDB")]
    assert check_stack_table(EffectiveTechStack(None, "none"), bad) == []


def test_only_the_stack_table_is_read_not_the_environments_table_under_it():
    """LIVE (2026-09-22): the section also holds an Environment | Description table; reading on
    into it flagged "Description" and every environment as outside the stack, and cost a
    needless correction call."""
    eff = EffectiveTechStack(NODE, "bu_default")
    section = TABLE.split("## SECURITY")[0] + (
        "\n### Deployment Architecture\n\n| Environment | Description |\n|---|---|\n"
        "| Development | ⚠️ [ASSUMPTION] — not specified in requirements. |\n| Production | Azure App Service |\n")
    assert check_stack_table(eff, section) == []
    # A table with no blank line before the next one is still just the first table's rows.
    tight = ("## TECHNOLOGY STACK\n| Layer | Technology |\n|---|---|\n| Database | MongoDB |\n"
             "Environment | Description\n--- | ---\nDevelopment | local\n")
    assert check_stack_table(eff, tight) == [StackViolation(layer="Database", technology="MongoDB")]


def test_short_names_match_whole_words_only():
    go = EffectiveTechStack(OWN, "project_selection")
    table = "## TECHNOLOGY STACK\n| Layer | Technology |\n|---|---|\n| Backend | Go 1.22 |\n| Database | MongoDB |\n"
    assert check_stack_table(go, table) == [StackViolation(layer="Database", technology="MongoDB")]


def test_the_correction_names_what_was_outside():
    note = correction_note([StackViolation("Database", "MongoDB")])
    assert "MongoDB (Database)" in note and NOT_COVERED in note


def test_the_section_is_stamped_once_and_leftovers_are_flagged():
    eff = EffectiveTechStack(NODE, "bu_default")
    stamped = annotate_stack_section(TABLE, eff, [])
    assert "## TECHNOLOGY STACK\n\nProject tech stack: **Node + Next.js** (the Business Unit default).\n" in stamped
    assert annotate_stack_section(stamped, eff, []) == stamped      # idempotent
    flagged = annotate_stack_section(TABLE, eff, [StackViolation("Database", "MongoDB")])
    assert "> **Outside the project's tech stack:** MongoDB (Database)." in flagged
    assert annotate_stack_section("no table here", eff, []) == "no table here"
