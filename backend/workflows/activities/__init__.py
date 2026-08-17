"""Stage setup and execution helpers, called directly by the Copilot.

  _base.py            per-stage connector kind, MCP tool selection, artifact
                      idempotency, and a re-export of write_and_notify
  pipeline_session.py the bridge that runs one stage inside a run's session

The per-agent `@activity.defn` wrappers that used to sit beside these were Temporal
bindings and are gone with it. Neither module here ever imported temporalio; they
are ordinary async functions, which is why the conversational path could always
call them without a workflow engine running.
"""
