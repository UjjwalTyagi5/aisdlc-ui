"""Convert Cobertura coverage XML to client-facing HTML coverage sections."""
from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from html import escape
from typing import Any, Dict, List

logger = logging.getLogger("testing_agent.coverage_html")


def _class_counts(cls: ET.Element) -> tuple[int, int]:
    """Return (statements, covered) for a Cobertura class node.

    Some producers, including .NET coverlet in common configurations, omit
    class-level lines-valid/lines-covered. In that case the authoritative data
    is the child <line hits="..."/> collection.
    """
    valid_attr = cls.get("lines-valid")
    covered_attr = cls.get("lines-covered")
    if valid_attr is not None or covered_attr is not None:
        return int(valid_attr or 0), int(covered_attr or 0)

    statements = 0
    covered = 0
    for line in cls.iter("line"):
        statements += 1
        try:
            if int(line.get("hits", "0") or 0) > 0:
                covered += 1
        except ValueError:
            continue
    return statements, covered


def _file_bucket(filename: str) -> str:
    normalized = filename.replace("\\", "/").lower()
    if "/views/" in f"/{normalized}" or normalized.endswith(".cshtml"):
        return "View / generated UI"
    if (
        normalized.endswith(".g.cs")
        or "/obj/" in f"/{normalized}"
        or "/bin/" in f"/{normalized}"
        or "/migrations/" in f"/{normalized}"
        or normalized.endswith("modelsnapshot.cs")
        or normalized.endswith(".designer.cs")
    ):
        return "Generated / build output"
    if normalized.endswith("program.cs") or normalized.endswith("startup.cs"):
        return "Application startup"
    return "Application source"


def _rate(covered: int, statements: int, fallback_rate: float = 0.0) -> float:
    if statements > 0:
        return covered / statements * 100.0
    return fallback_rate


def parse_per_file_coverage(xml_path: str) -> List[Dict[str, Any]]:
    """Parse Cobertura XML and return one aggregated row per file."""
    if not os.path.exists(xml_path):
        return []
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        logger.warning(f"parse_per_file_coverage: malformed XML at {xml_path}: {exc}")
        return []

    by_file: Dict[str, Dict[str, Any]] = {}
    for cls in tree.getroot().iter("class"):
        filename = cls.get("filename") or ""
        if not filename:
            continue
        statements, covered = _class_counts(cls)
        row = by_file.setdefault(filename, {
            "filename": filename,
            "statements": 0,
            "covered": 0,
            "bucket": _file_bucket(filename),
        })
        row["statements"] += statements
        row["covered"] += covered

    out: List[Dict[str, Any]] = []
    for row in by_file.values():
        statements = int(row["statements"])
        covered = int(row["covered"])
        missed = max(statements - covered, 0)
        out.append({
            "filename": row["filename"],
            "coverage_pct": _rate(covered, statements),
            "statements": statements,
            "covered": covered,
            "missed": missed,
            "bucket": row["bucket"],
        })
    return out


def _color_for(pct: float) -> str:
    if pct < 50:
        return "background:#ffe5e5;color:#a00"
    if pct < 80:
        return "background:#fff4d0;color:#7a5800"
    return "background:#e3f7e0;color:#1c5c1c"


def _summary_card(label: str, value: str, subtext: str = "") -> str:
    return (
        '<div style="border:1px solid #d0d7de;padding:10px;border-radius:6px;min-width:150px">'
        f'<div style="font-size:11px;text-transform:uppercase;color:#57606a">{escape(label)}</div>'
        f'<div style="font-size:22px;font-weight:700">{escape(value)}</div>'
        f'<div style="font-size:12px;color:#57606a">{escape(subtext)}</div>'
        '</div>'
    )


def _coverage_table(title: str, rows_data: List[Dict[str, Any]], note: str = "") -> str:
    rows: List[str] = []
    for item in sorted(rows_data, key=lambda f: (f.get("coverage_pct", 100.0), f.get("filename", ""))):
        filename = escape(str(item.get("filename") or ""))
        statements = int(item.get("statements") or 0)
        covered = int(item.get("covered") or 0)
        missed = int(item.get("missed") or 0)
        pct = float(item.get("coverage_pct") or 0.0)
        rows.append(
            f'<tr><td>{filename}</td>'
            f'<td style="text-align:right">{statements}</td>'
            f'<td style="text-align:right">{covered}</td>'
            f'<td style="text-align:right">{missed}</td>'
            f'<td style="{_color_for(pct)};text-align:right">{pct:.1f}%</td></tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="5">No files in this category.</td></tr>')

    return (
        f'<h4 style="margin:18px 0 6px">{escape(title)}</h4>'
        + (f'<p style="margin:0 0 8px;color:#57606a">{escape(note)}</p>' if note else "")
        + '<table border="1" cellspacing="0" cellpadding="5" '
        'style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:12px;width:100%">'
        '<thead style="background:#f6f8fa"><tr>'
        '<th style="text-align:left">File</th><th>Statements</th><th>Covered</th><th>Missed</th><th>Coverage %</th>'
        '</tr></thead><tbody>'
        + "".join(rows)
        + '</tbody></table>'
    )


def coverage_xml_to_html(xml_path: str) -> str:
    if not os.path.exists(xml_path):
        return '<div class="coverage-unavailable">Coverage data unavailable: file not found.</div>'
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        logger.warning(f"coverage XML parse error: {exc}")
        return f'<div class="coverage-unavailable">Coverage data unavailable: malformed XML ({exc}).</div>'

    files = parse_per_file_coverage(xml_path)
    overall = float(root.get("line-rate") or 0) * 100.0
    source_files = [f for f in files if f.get("bucket") == "Application source"]
    non_source_files = [f for f in files if f.get("bucket") != "Application source"]
    source_statements = sum(int(f.get("statements") or 0) for f in source_files)
    source_covered = sum(int(f.get("covered") or 0) for f in source_files)
    source_rate = _rate(source_covered, source_statements, overall)

    cards = "".join([
        _summary_card("Overall line coverage", f"{overall:.1f}%", "All files from coverage XML"),
        _summary_card("Application source coverage", f"{source_rate:.1f}%", f"{source_covered}/{source_statements} statements"),
        _summary_card("Source files", str(len(source_files)), "Business/testable code files"),
        _summary_card("View/generated files", str(len(non_source_files)), "Shown separately"),
    ])

    return (
        '<div class="coverage-section" style="font-family:Arial,sans-serif">'
        '<h3 style="margin-bottom:6px">Coverage Report</h3>'
        '<p style="color:#57606a;margin-top:0">'
        'Statement counts are computed from Cobertura line-hit data when class-level totals are absent. '
        'Duplicate class entries are aggregated by file to avoid repeated rows.'
        '</p>'
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin:12px 0">{cards}</div>'
        + _coverage_table("Application Source Coverage", source_files, "Primary client-facing metric for unit-test effectiveness.")
        + _coverage_table("View, Startup, and Generated Coverage", non_source_files, "Tracked for transparency, but usually not the main unit-test quality metric.")
        + '</div>'
    )
