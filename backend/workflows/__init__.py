"""Stage-execution helpers shared by the agent runtime.

WAS a Temporal workflow package. Temporal is gone — `sdlc_workflow.py`,
`activity_dispatch.py`, `execution_plan.py` and the per-agent `@activity.defn`
wrappers went with it. What remains under `activities/` never had a Temporal
decorator on it and is not workflow machinery: it is the code that knows how to set
a stage up and run it, and the conversational Copilot path calls it directly.

The package keeps its name because the modules under it are imported by path from
`agents_orchestrator/orchestrator/copilot_api.py`; renaming it would be churn for
its own sake.
"""
