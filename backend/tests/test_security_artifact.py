"""SecurityArtifact model unit tests — Task 1 of Security Agent plan."""
from shared.models.artifacts import SecurityArtifact


def test_security_artifact_defaults():
    art = SecurityArtifact()
    assert art.scope is None
    assert art.dependency_findings == []
    assert art.code_findings == []
    assert art.secret_findings == []
    assert art.risk_score is None
    assert art.remediation_plan == []
    assert art.security_sign_off is False
    assert art.scan_summary is None
    assert art.version == 1


def test_security_artifact_with_findings():
    art = SecurityArtifact(
        scope="full",
        dependency_findings=[{
            "cve": "CVE-2024-1234",
            "severity": "high",
            "package": "requests",
            "installed_version": "2.28.0",
            "fixed_version": "2.31.0",
        }],
        code_findings=[{
            "rule_id": "python.lang.security.audit.eval-detected",
            "severity": "warning",
            "file": "src/utils.py",
            "line": 15,
            "message": "Use of eval() detected",
        }],
        secret_findings=[{
            "rule_id": "generic-api-key",
            "file": "config.py",
            "line": 3,
            "match": "REDACTED",
        }],
        risk_score="high",
        security_sign_off=False,
        scan_summary="1 high CVE, 1 code finding, 1 secret detected",
    )
    assert len(art.dependency_findings) == 1
    assert art.dependency_findings[0]["cve"] == "CVE-2024-1234"
    assert art.risk_score == "high"
    assert art.security_sign_off is False


def test_security_artifact_roundtrip():
    art = SecurityArtifact(scope="dependency_scan", risk_score="low", version=1)
    data = art.model_dump()
    restored = SecurityArtifact(**data)
    assert restored.scope == "dependency_scan"
    assert restored.risk_score == "low"
