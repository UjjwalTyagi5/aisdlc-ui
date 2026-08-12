"""Capability tags for native (Tier 0) tools, keyed by tool name (decision DP1).

Tags live here — a single auditable surface — rather than as decorators on each
@tool, so we never churn the hundreds of existing tool definitions. The tool NAME
is the join key (matches `tool.name` on the LangChain tool objects assembled into
each agent's `tools` list).
"""
from __future__ import annotations

# A tool may provide ONE capability (str) or SEVERAL (list[str]) — e.g. the design
# agent's generate_architecture produces HLD + LLD + API contract + ADRs + tech stack.
_Tag = "str | list[str]"

NATIVE_TAGS: dict[str, dict[str, "str | list[str]"]] = {
    "requirements": {
        # ingestion
        "upload_file": "req.ingest",
        # document generation
        "generate_brd": "doc.generate.brd",
        "generate_mom": "doc.generate.mom",
        "generate_pdd": "doc.generate.pdd",
        "generate_risk_register": "doc.generate.risk",
        "generate_planning_sheet": "doc.export.xlsx",
        "markdowntodoc": "doc.export.docx",
        # story generation / revision / normalisation
        "generate_user_stories": "story.generate",
        "generate_stories_from_brd": "story.generate",
        "revise_user_stories": "story.revise",
        "normalize_acceptance_criteria": "story.ac.normalize",
        # quality analysis (ambiguity / testability / INVEST / weak words)
        "run_nlp_quality_check": "req.quality.analyze",
        "run_requirement_smell_check": "req.quality.analyze",
        "run_spectral_lint": "design.api.lint",
        # gap detection / epic grouping
        "detect_requirement_gaps": "req.gap.detect",
        "identify_epics": "story.epic.identify",
        # payload (maps story -> source) + artifact persistence
        "build_requirements_payload": ["req.payload.build", "traceability.map"],
        "write_requirements_artifact": "artifact.write",
        # board read operations
        "list_board_projects": "board.read",
        "list_board_groups": "board.read",
        "list_board_states": "board.read",
        "list_board_items": "board.read",
        "list_board_items_by_state": "board.read",
        "fetch_board_item_detail": "board.read",
        # board hierarchy
        "fetch_board_hierarchy": "board.hierarchy",
        # board write operations
        "create_board_project": "board.write",
        "create_board_item": "board.write",
        "update_board_item": "board.write",
        "delete_board_item": "board.write",
        "move_board_item_state": "board.write",
        "write_stories_to_board": "board.write",
        "write_acceptance_criteria_to_board": "board.write",
        "write_back_normalized_to_board": "board.write",
        # board comment
        "add_board_comment": "board.comment",
    },
    "design": {
        # one generation tool produces several SDD sections
        "generate_architecture": [
            "design.hld.generate", "design.lld.generate",
            "design.api.contract.generate", "design.adr.generate",
            "design.tech.stack.recommend",
        ],
        # brownfield / existing-system analysis
        "generate_architecture_from_context": "design.system.analyze",
        # diagrams (C4 / sequence / ERD via Kroki; Mermaid is client-side)
        "render_diagram_via_kroki": "design.diagram.render",
        # OpenAPI lint + DB schema validation
        "run_spectral_lint": "design.api.lint",
        "validate_database_schema": "design.schema.validate",
        # artifact persistence also carries the OWASP checklist + design->requirement map
        "write_design_artifact": [
            "artifact.write", "design.security.design.checklist", "traceability.map",
        ],
    },
    "development": {
        # ── repo.read — filesystem / VCS context reads ───────────────────────
        "read_file": "repo.read",
        "list_directory": "repo.read",
        "get_ado_context": "repo.read",
        "get_github_context": "repo.read",
        "list_ado_projects": "repo.read",
        "list_ado_repos": "repo.read",
        "list_ado_branches": "repo.read",
        "list_github_repos": "repo.read",
        "list_github_branches": "repo.read",
        # ── repo.write — filesystem + VCS repo mutation ───────────────────────
        "write_file": "repo.write",
        "delete_file": "repo.write",
        "create_ado_repo": "repo.write",
        # ── repo.clone ────────────────────────────────────────────────────────
        "clone_repo": "repo.clone",
        # ── repo.search ───────────────────────────────────────────────────────
        "search_codebase": "repo.search",
        # ── code.edit ─────────────────────────────────────────────────────────
        "edit_file": "code.edit",
        # ── code.generate — scaffold / template / migration generation ────────
        "init_project_structure": "code.generate",
        "generate_project_scaffold": "code.generate",
        "generate_component": "code.generate",
        "generate_api_endpoint": "code.generate",
        "generate_database_migration": "code.generate",
        # ── code.build — arbitrary shell invocation (compile, test-run, build) -
        "run_command": "code.build",
        # ── code.execute — sandboxed interpreter / REPL ───────────────────────
        "execute_code_in_sandbox": "code.execute",
        # ── code.lint ─────────────────────────────────────────────────────────
        "lint_and_validate_code": "code.lint",
        # ── vcs.* ─────────────────────────────────────────────────────────────
        "create_feature_branch": "vcs.branch.create",
        "git_commit": "vcs.commit",
        "push_branch": "vcs.commit",
        "create_pr": "vcs.pr.create",
        "create_github_pr_tool": "vcs.pr.create",
        "mark_pr_ready": "vcs.pr.ready",
        "add_pr_comment_to_work_items": "vcs.pr.comment",
        # ── board.* ───────────────────────────────────────────────────────────
        "get_work_item": "board.read",
        "list_work_items": "board.read",
        "update_work_item_state": "board.write",
        # ── artifact.* ────────────────────────────────────────────────────────
        "read_design_artifact": "artifact.read",
        "submit_development_artifacts": "artifact.write",
        "write_development_artifact": "artifact.write",
    },
    "code_review": {
        # ── review.diff.analyze ───────────────────────────────────────────────
        "analyze_diff": "review.diff.analyze",
        # ── repo context (read-only) ──────────────────────────────────────────
        "read_repo_file": "repo.read",
        "search_repo": "repo.search",
        # ── quality.sast.scan ─────────────────────────────────────────────────
        "run_semgrep_scan": "quality.sast.scan",
        # ── upstream-artifact alignment ───────────────────────────────────────
        "read_requirements_payload": "review.requirements.coverage.map",
        "read_design_artifacts": "review.design.conformance.check",
        # ── artifact.write (also emits severity.assess + merge.recommend) ─────
        "submit_code_review": "artifact.write",
    },
    "security": {
        # ── layered scans ─────────────────────────────────────────────────────
        "scan_dependencies": "quality.sca.scan",
        "generate_sbom": "quality.sbom.generate",
        "scan_code": "quality.sast.scan",
        "scan_secrets": "quality.secret.scan",
        # ── repo context (read-only) ──────────────────────────────────────────
        "read_repo_file": "repo.read",
        "search_repo": "repo.search",
        "read_design_artifacts": "artifact.read",
        # ── artifact.write (also emits dedup/contextualize/risk.score/remediation/signoff) ─
        "submit_security_review": "artifact.write",
    },
    "testing": {
        # Node-pipeline agent (not @tool-based) — these map the pipeline's stages to
        # capabilities so the Agents & Capabilities panel shows a real testing kit and
        # the gap-preflight for the required set is clean.
        "plan_tests": "test.plan",
        "generate_tests": "test.generate",
        "run_tests": "test.run",
        "measure_coverage": "test.coverage",
        "validate_api_contract": "test.api.contract.test",
        "analyze_failures": "test.failure.analyze",
        "evaluate_quality_gate": "test.quality.gate.evaluate",
        "generate_qa_report": "test.qa.report",
        "submit_testing_artifacts": "artifact.write",
    },
    "deployment": {
        "inspect_repo": "deploy.readiness.assess",
        "read_upstream_artifacts": "deploy.gate.aggregate",
        "read_repo_file": "repo.read",
        "search_repo": "repo.search",
        "stage_deploy_file": "deploy.plan",
        "open_deploy_pr": "vcs.pr.create",
        # artifact.write also emits risk.score / rollback.plan / compliance.evidence / release.decision
        "submit_release": "artifact.write",
        # Provider-aware CI trigger plus the two concrete providers behind it.
        "trigger_deployment_pipeline": "deploy.pipeline.trigger",
        "trigger_azure_pipeline": "deploy.pipeline.trigger",
        "trigger_github_actions_workflow": "deploy.pipeline.trigger",
    },
    "documentation": {
        "inspect_repo": "repo.read",
        "read_repo_file": "repo.read",
        "search_repo": "repo.search",
        "generate_changelog": "doc.changelog.generate",
        "read_upstream_artifacts": "artifact.read",
        # save_document also emits doc.set.compile / doc.rtm.generate / doc.compliance.evidence
        "save_document": "doc.generate",
        "open_docs_pr": "vcs.pr.create",
        # SharePoint destination — docs.publish and doc.ingest were already in the
        # taxonomy with zero call sites; these are the first.
        "publish_to_sharepoint": "docs.publish",
        "list_sharepoint_documents": "doc.ingest",
        "ingest_sharepoint_document": "doc.ingest",
    },
    # Other agents tagged in subsequent cohorts — empty until their fan-out tasks land.
}


def _as_caps(value: "str | list[str]") -> list[str]:
    """Normalize a tag value (single cap or list of caps) to a list."""
    return [value] if isinstance(value, str) else list(value)


def native_capabilities(agent_id: str) -> set[str]:
    """Set of capabilities the agent provides natively (from its tagged tools)."""
    caps: set[str] = set()
    for value in NATIVE_TAGS.get(agent_id, {}).values():
        caps.update(_as_caps(value))
    return caps
