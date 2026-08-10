"""Temporal activity wrappers for the SDLC pipeline (M5-04).

Each module exposes one @activity.defn async function:
  requirements_activity  -> run_requirements_activity
  design_activity        -> run_design_activity
  development_activity   -> run_development_activity
  testing_activity       -> run_testing_activity  (plan 05)
  emit_escalation_activity -> emit_escalation_activity  (plan 05, SLA breach)

_base.py provides the shared idempotency helper check_existing_artifact
and re-exports write_and_notify from artifact_service for a single import
surface in activity wrappers.
"""
