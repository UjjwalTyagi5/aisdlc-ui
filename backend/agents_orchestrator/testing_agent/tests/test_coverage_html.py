"""coverage_html: Cobertura XML → human-readable HTML table."""
from __future__ import annotations

from pathlib import Path

from agents_orchestrator.testing_agent.tools.coverage_html import (
    coverage_xml_to_html,
    parse_per_file_coverage,
)


COBERTURA_FIXTURE = """<?xml version="1.0" ?>
<coverage line-rate="0.75" branch-rate="0.5">
  <packages>
    <package name="pkg1">
      <classes>
        <class filename="src/a.py" line-rate="0.9" lines-covered="9" lines-valid="10"/>
        <class filename="src/b.py" line-rate="0.4" lines-covered="4" lines-valid="10"/>
      </classes>
    </package>
  </packages>
</coverage>"""


def test_xml_to_html_renders_table_with_color_coded_rows(tmp_path):
    xml_path = tmp_path / "coverage.xml"
    xml_path.write_text(COBERTURA_FIXTURE)
    html = coverage_xml_to_html(str(xml_path))
    assert "<table" in html
    assert "src/a.py" in html
    assert "src/b.py" in html
    assert "90.0" in html or "90%" in html
    assert "40.0" in html or "40%" in html
    # Color hint: <50 should be red, 50-80 yellow, >80 green
    assert ("red" in html.lower()) or ("danger" in html.lower())


def test_xml_to_html_handles_missing_file():
    html = coverage_xml_to_html("/path/does/not/exist.xml")
    assert "unavailable" in html.lower() or "not found" in html.lower()


def test_xml_to_html_handles_malformed_xml(tmp_path):
    xml_path = tmp_path / "bad.xml"
    xml_path.write_text("<not really xml")
    html = coverage_xml_to_html(str(xml_path))
    assert "unavailable" in html.lower() or "error" in html.lower()


def test_dotnet_cobertura_lines_are_aggregated_without_question_marks(tmp_path):
    xml_path = tmp_path / "coverage.xml"
    xml_path.write_text("""<?xml version="1.0" ?>
<coverage line-rate="0.5">
  <packages><package><classes>
    <class filename="Services/CaseService.cs" line-rate="0.5">
      <lines>
        <line number="10" hits="1"/>
        <line number="11" hits="0"/>
      </lines>
    </class>
    <class filename="Services/CaseService.cs" line-rate="1.0">
      <lines>
        <line number="20" hits="3"/>
      </lines>
    </class>
    <class filename="Views/Cases/Create.cshtml" line-rate="0">
      <lines><line number="1" hits="0"/></lines>
    </class>
  </classes></package></packages>
</coverage>""")

    rows = parse_per_file_coverage(str(xml_path))
    service = next(r for r in rows if r["filename"] == "Services/CaseService.cs")
    assert service["statements"] == 3
    assert service["covered"] == 2
    assert round(service["coverage_pct"], 1) == 66.7

    html = coverage_xml_to_html(str(xml_path))
    assert "?" not in html
    assert html.count("Services/CaseService.cs") == 1
    assert "Application Source Coverage" in html
    assert "View, Startup, and Generated Coverage" in html


def test_migrations_are_classified_as_generated_not_application_source(tmp_path):
    xml_path = tmp_path / "coverage.xml"
    xml_path.write_text("""<?xml version="1.0" ?>
<coverage line-rate="0.5">
  <packages><package><classes>
    <class filename="Migrations/20260429054147_InitialCreate.cs">
      <lines><line number="1" hits="0"/></lines>
    </class>
    <class filename="Services/ActionLogService.cs">
      <lines><line number="1" hits="1"/></lines>
    </class>
  </classes></package></packages>
</coverage>""")

    rows = parse_per_file_coverage(str(xml_path))
    migration = next(r for r in rows if "Migrations" in r["filename"])
    service = next(r for r in rows if "Services" in r["filename"])

    assert migration["bucket"] == "Generated / build output"
    assert service["bucket"] == "Application source"
