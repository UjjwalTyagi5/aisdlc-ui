from config.context_broker import _fmt_requirements


def test_fmt_board_shape():
    out = _fmt_requirements({
        "project": "carelon",
        "stories": [{"title": "Login", "acceptance_criteria": "Given creds..."}],
        "non_functional_requirements": ["p95 < 200ms"],
        "gap_report": "Missing logout AC",
    })
    assert "carelon" in out
    assert "Login" in out
    assert "Gap Report" in out


def test_fmt_artifact_shape():
    out = _fmt_requirements({
        "brd_content": "## BRD\nThe system must authenticate users.",
        "user_stories": [{"title": "User can log in", "acceptance_criteria": "Given..."}],
        "acceptance_criteria": ["200ms response"],
        "risk_register": [{"risk": "PII leak", "mitigation": "encrypt"}],
    })
    assert "User can log in" in out
    assert "BRD" in out
    assert "Risk" in out


def test_fmt_empty_payload():
    out = _fmt_requirements({})
    assert isinstance(out, str)
    assert "REQUIREMENTS CONTEXT" in out
