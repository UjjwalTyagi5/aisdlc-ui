"""Frozen controlled vocabulary for agent capabilities (design decision D13).

A capability string may be used ANYWHERE in the platform only if it appears in
CAPABILITIES below. New capabilities are added by editing this file (a versioned,
reviewed change) — never ad hoc. NATIVE_ONLY lists capabilities that BYO (Tier 2)
servers may never provide (D9): repo/code mutation and platform-internal writes.
"""
from __future__ import annotations

from typing import Iterable

# --- Controlled vocabulary (mirrors docs/.../mcp/integration/02 §"Capability vocabulary") ---
CAPABILITIES: frozenset[str] = frozenset({
    # board.*
    "board.read", "board.write", "board.comment", "board.hierarchy",
    # repo.* / vcs.*
    "repo.read", "repo.write", "repo.clone", "repo.search",
    "vcs.branch.create", "vcs.commit", "vcs.pr.create", "vcs.pr.comment", "vcs.pr.ready",
    # doc.*
    "doc.generate.brd", "doc.generate.mom", "doc.generate.pdd", "doc.generate.risk",
    "doc.export.docx", "doc.export.pdf", "doc.export.xlsx", "doc.ingest",
    # documentation agent (standalone): changelog + general doc generation
    "doc.changelog.generate", "doc.generate",
    # story.* / req.* / nfr.*
    "story.generate", "story.revise", "story.ac.normalize", "story.gap.detect", "story.epic.identify",
    "req.ingest", "req.quality.analyze", "req.gap.detect", "req.payload.build",
    "nfr.elicit",
    # design.*
    "design.hld.generate", "design.lld.generate", "design.adr.generate", "design.diagram.render",
    "design.schema.validate", "design.api.contract.generate", "design.api.lint",
    "design.system.analyze", "design.tech.stack.recommend", "design.security.design.checklist",
    # code.*
    "code.generate", "code.edit", "code.lint", "code.format", "code.build",
    "code.execute", "code.search",
    # quality.*
    "quality.sast.scan", "quality.sca.scan", "quality.sbom.generate", "quality.secret.scan",
    "quality.iac.scan", "quality.complexity", "quality.dupe.detect", "quality.license.scan",
    # review.*
    "review.diff.analyze", "review.requirements.coverage.map", "review.design.conformance.check",
    "review.severity.assess", "review.merge.recommend",
    # sec.*
    "sec.finding.dedup", "sec.severity.contextualize", "sec.owasp.map", "sec.risk.score",
    "sec.remediation.plan", "sec.signoff",
    # test.*
    "test.plan", "test.generate", "test.run", "test.coverage", "test.api.contract.test",
    "test.failure.analyze", "test.quality.gate.evaluate", "test.qa.report",
    # deploy.*
    "deploy.readiness.assess", "deploy.gate.aggregate", "deploy.plan", "deploy.rollback.plan",
    "deploy.iac.validate", "deploy.env.health.check", "deploy.pipeline.trigger", "deploy.release.decision",
    "deploy.risk.score", "deploy.migration.verify", "deploy.flag.coordinate", "deploy.compliance.evidence",
    # docs.*
    "docs.aggregate", "docs.compile", "docs.api.reference.generate", "docs.changelog.generate",
    "docs.release.notes.generate", "docs.run.summary", "docs.publish",
    # cross-cutting
    "traceability.map",
    "artifact.read", "artifact.write",
})

# BYO servers can NEVER provide these (D9). Code/repo mutation + platform-internal writes.
NATIVE_ONLY: frozenset[str] = frozenset({
    "repo.write", "repo.clone",
    "vcs.branch.create", "vcs.commit", "vcs.pr.create", "vcs.pr.ready",
    "code.edit", "code.execute",
    "board.write",
    "artifact.read", "artifact.write",
})


def is_valid(cap: str) -> bool:
    return cap in CAPABILITIES


def is_native_only(cap: str) -> bool:
    return cap in NATIVE_ONLY


def assert_valid(caps: Iterable[str]) -> None:
    """Raise ValueError listing any capabilities not in the controlled vocabulary."""
    unknown = sorted({c for c in caps if c not in CAPABILITIES})
    if unknown:
        raise ValueError(f"Unknown capabilities (not in taxonomy): {', '.join(unknown)}")
