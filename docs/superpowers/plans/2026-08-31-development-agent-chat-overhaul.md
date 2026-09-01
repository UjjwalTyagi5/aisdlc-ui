# Development Agent — Chat Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three concrete gaps the user hit live testing the Development Agent
(2026-08-31, same day as the original verification plan): the agent never shows the code
it changes even though the rendering stack for it already exists, every session pays for
the full upstream Requirements/Design payload even when it's just continuing work on an
already-pulled repo, and a slow turn can race a frontend reconnect into what reads as a
duplicated reply.

**Architecture:** No rewrite, no new dependencies. `markdown-message.tsx` (react-markdown +
syntax highlighting) and `DiffViewer` (Monaco diff editor) both already exist and are both
already used elsewhere in this app — this plan wires them into the Development chat path
and fixes two backend behaviors. Confirmed by direct code inspection this session, not
inferred.

**Tech Stack:** FastAPI + LangGraph (backend), Next.js/React + Zod (frontend), pytest +
Vitest/Jest for tests.

**Prior context:** This session already completed the Development Agent's 10-task
access-hardening/verification plan
(`docs/superpowers/plans/2026-08-31-development-agent-verification.md`) plus 6 out-of-plan
live bugs found and fixed during manual verification. Full history, including this plan's
own approved design, is in `desicions and issues.txt` at the repo root (exact filename,
typo preserved) — continue that file's Issue numbering (next is Issue 7) and its
fix→test→commit→log format when this plan's tasks land.

## Global Constraints

- **Every `pytest` invocation needs `PYTHONPATH=.`, run from `backend/`, using
  `backend/.venv/Scripts/python.exe`** — NOT system Python (system Python's setuptools
  breaks on the `docxtpl` import chain: `AttributeError: module 'pkgutil' has no attribute
  'ImpImporter'`). Example: `cd backend && PYTHONPATH=. ./.venv/Scripts/python.exe -m
  pytest tests/... -v`.
- The backend runs on port 8004 locally (moved 8001→8002→8003→8004 this session due to a
  recurring Windows/Hyper-V NAT "ghost port" phenomenon — a phantom `LISTENING` entry for a
  dead PID that silently intercepts some real traffic). After any task that touches backend
  code, the backend must be restarted and **self-verified** before being declared ready:
  `curl` a real endpoint and confirm the request shows up in the tailed log, not just that
  curl returned 200. Never tell the user to retry based on "the process is running" alone.
- No new abstraction where an existing one already does the job: Task 1 reuses the existing
  `broadcast_log`/`manager.broadcast` WS-push pattern already used by every other file-tool
  event in `file_tools.py`; Task 2 reuses the existing `DiffViewer` component and the
  existing `mapWsToSseEvent`/`StreamEvent` allowlist pattern in `ws-to-sse.ts` — do not
  invent a second WS bridge or a second markdown/diff renderer.
- `ws-to-sse.ts` is a deliberate security allowlist (see its own docstring, T-M4-14): every
  unrecognized WS message type is discarded so the backend can never inject an arbitrary
  payload into the SSE stream the browser trusts. The new `file_diff` → `code.diff` mapping
  must follow the exact same explicit-case, Zod-validated pattern as the existing
  `file_generated` → `artifact.updated` case — no passthrough of raw backend JSON.
- Every task ends green (backend: `pytest`; frontend: the project's existing test runner for
  touched files) before its commit.
- Do not touch the already-fixed ADO credential resolution, WS ticket local-mode, gpt-5
  temperature, or dead-tool-removal code paths from the prior plan/session — they are done
  and verified; this plan's tasks are additive to different code.

---

### Task 1: Backend — emit a structured diff event from write_file/edit_file

**Files:**
- Modify: `backend/agents_orchestrator/development_agent/tools/file_tools.py`
  (`write_file` at line 71, `edit_file` at line 172)
- Modify: `backend/config/ws_helper.py` (add a sibling to `broadcast_log`, defined at line 109)
- Modify: `backend/agents_orchestrator/development_agent/prompts/dev_agent_prompt.py`
  (the "CRITICAL — DO NOT PASTE CODE IN CHAT TEXT" block at lines 145-150)
- Test: `backend/tests/test_development_agent_tools.py` (existing file from the prior
  plan's Task 5 — add to it, don't replace it) or a new
  `backend/tests/development/test_file_diff_events.py`, your choice, matching whichever
  existing test in that area most resembles this one.

**Interfaces:**
- Consumes: `resolve_safe_path`/`_get_work_dir` (unchanged — do not touch path-safety
  logic), the existing `broadcast_log(manager, message, level)` pattern for the thread-safe
  MAIN_LOOP scheduling dance it already implements (`ws_helper.py:109` and following) — the
  new broadcast helper must use the identical scheduling mechanism, not a new one.
- Produces: a new WS message type, broadcast via `manager.broadcast(...)` (same call shape
  as `_broadcast_files_updated` in `development_agent_api.py:795`), with this **exact**
  contract — Task 2 consumes it verbatim:
  ```json
  {
    "type": "file_diff",
    "session_id": "<str>",
    "path": "<relative_path, forward-slash>",
    "original": "<full previous file content, or \"\" if newly created>",
    "modified": "<full new file content>",
    "change_kind": "created" | "edited"
  }
  ```
  `original`/`modified` are FULL file contents, not a unified-diff string — `DiffViewer`
  (Task 2) is a Monaco `DiffEditor` that computes and renders the diff itself from two full
  strings; do not pre-compute a textual diff here.

**Steps:**

- [ ] **Step 1: Add `broadcast_file_diff` to `ws_helper.py`**

  Add a function alongside `broadcast_log` (same file, same thread-safe MAIN_LOOP scheduling
  pattern — read `broadcast_log`'s body first and mirror its dispatch mechanism exactly,
  since `write_file`/`edit_file` are sync functions calling into async broadcast machinery,
  same as `broadcast_log` already handles). Signature:
  `broadcast_file_diff(manager, path: str, original: str, modified: str, change_kind: str) -> None`.
  Resolves `session_id` the same way `broadcast_log` does (`get_session_id()`). Broadcasts
  the exact JSON contract above via `{"type": "file_diff", ...}`.

- [ ] **Step 2: Wire `write_file` (file_tools.py:71-107)**

  Before writing, if `full_path.exists()`, read its current text as `original` (same
  encoding/error handling as `read_file`'s `full_path.read_text(encoding="utf-8",
  errors="replace")`); if the file doesn't exist yet, `original = ""`. After a successful
  write, call `broadcast_file_diff(manager, relative_path, original, content, "created" if
  original == "" else "edited")` — right after (or in place of, your call — both send a
  distinct WS message today) the existing `broadcast_log(manager, f"Wrote: ...")` call.
  Keep the existing `broadcast_log` call too; this is an additional event, not a
  replacement — the activity feed and the new diff-card event serve different UI surfaces.

- [ ] **Step 3: Wire `edit_file` (file_tools.py:172-225)**

  This function already has `content` (pre-edit) and `new_content` (post-edit) in scope
  right before its existing `full_path.write_text(new_content, ...)` call. After the write
  succeeds, call `broadcast_file_diff(manager, relative_path, content, new_content,
  "edited")`.

- [ ] **Step 4: Relax the "no code in chat" prompt rule (dev_agent_prompt.py:145-150)**

  Do not remove the rule — it still correctly stops the model from wastefully duplicating
  code between its prose and the tool call. Add one sentence clarifying WHY the rule is
  fine for the user: a diff card now renders automatically in the chat UI for every
  `write_file`/`edit_file` call, so the user sees the code without the model ever needing
  to paste it. Something in this shape (adjust wording to fit the surrounding block's
  voice):
  ```
  CRITICAL — DO NOT PASTE CODE IN CHAT TEXT:
    Pass code directly as a tool argument — never write code blocks in your message.
    A diff card showing exactly what changed renders automatically in the chat UI for
    every file you write or edit — the user already sees the code. Your text response
    should be a brief plain-language summary only ("Added the login form validation").
    ...
  ```

- [ ] **Step 5: Test**

  Cover: `write_file` on a new file emits `file_diff` with `original=""`,
  `change_kind="created"`; `write_file` overwriting an existing file emits `original=<old
  content>`, `change_kind="edited"`; `edit_file` emits `original`/`modified` matching the
  pre/post content exactly. Mock `manager.broadcast` (or whatever the chosen
  `broadcast_file_diff` dispatch mechanism ends up being) the same way the existing test
  file mocks WS broadcasts for these tools — follow its established pattern rather than
  inventing a new mocking approach.

  Run (from `backend/`):
  ```
  PYTHONPATH=. ./.venv/Scripts/python.exe -m pytest tests/test_development_agent_tools.py tests/development/ -v
  ```

---

### Task 2: Frontend — render diff cards inline in the chat drawer

**Files:**
- Modify: `frontend/lib/schemas/stream.ts` (add a new `StreamEvent` union member)
- Modify: `frontend/lib/bff/ws-to-sse.ts` (add a `case "file_diff":` following the exact
  pattern of the existing `case "file_generated":` at line 83)
- Modify: `frontend/components/app/agent-chat-drawer.tsx` (render the new event type)
- Test: `frontend/__tests__/bff/ws-to-sse.test.ts` (existing file — add cases to it) and
  whatever existing test file covers `agent-chat-drawer.tsx`'s message rendering, if one
  exists; if none does, a new test file matching the project's existing component-test
  conventions (check a sibling component's test file for the pattern — `diff-viewer.tsx`
  likely has one on the Code Review page).

**Interfaces:**
- Consumes: the exact `file_diff` WS event contract Task 1 produces (see Task 1's
  Interfaces section — do not re-derive it, use it verbatim). Consumes the existing
  `DiffViewer` component (`components/app/diff-viewer.tsx`) unmodified — its props are
  `original: string`, `modified: string`, `filename?: string`, and it defaults to a
  collapsed-friendly `showToolbar`/toggle UI already; do not fork or modify `DiffViewer`
  itself.
- Produces: a new `StreamEvent` variant (name it `code.diff` to match the existing
  dot-namespaced convention seen in `step.output.delta`, `artifact.updated`,
  `run.completed`) with fields `{ type: "code.diff", runId, path, original, modified,
  changeKind, at }`, Zod-validated the same way every other variant in `stream.ts` is.

**Steps:**

- [ ] **Step 1: Add the `code.diff` StreamEvent variant**

  In `lib/schemas/stream.ts`, add a new object schema to the `StreamEvent` discriminated
  union following the exact shape/conventions of the neighboring variants (check how
  `artifact.updated` is defined immediately above/below it in that file). Fields: `type:
  z.literal("code.diff")`, `runId: string`, `path: string`, `original: string`, `modified:
  string`, `changeKind: z.enum(["created", "edited"])`, `at: string`.

- [ ] **Step 2: Map `file_diff` → `code.diff` in `ws-to-sse.ts`**

  Add a case mirroring `case "file_generated":` (line 83) exactly in structure: pull
  `path`/`original`/`modified`/`change_kind` off `wsMsg` with the same defensive
  `typeof === "string"` guards already used throughout this file, build the `code.diff`
  event, return `validateOrNull(event)`. Update the file's own mapping-table docstring
  comment (lines 9-18) to add the new `file_diff → code.diff` row — keep that table
  accurate, it's the file's own contract documentation.

- [ ] **Step 3: Render diff cards in the chat drawer**

  In `agent-chat-drawer.tsx`, find where incoming stream events currently get turned into
  rendered chat items (the same place that already handles `step.output.delta` text
  accumulation into the assistant's message bubble — read that logic first to match its
  existing patterns for keying/ordering items in the timeline). Add handling for
  `code.diff` events: render each as its own item in the message timeline (not merged into
  the text bubble), using `<DiffViewer original={...} modified={...} filename={path}
  showToolbar />`, wrapped in a collapsed-by-default expandable container (check if this
  codebase already has a collapsible/accordion primitive in `components/ui/` — reuse it
  rather than hand-rolling one). One card per file per turn; if the same path gets multiple
  `code.diff` events in one turn (e.g. `edit_file` called twice on the same file), the
  later event's `modified` wins but `original` stays the FIRST event's `original` for that
  turn, so the card shows the net change across the whole turn, not intermediate steps —
  keep a per-turn `Map<path, {original, modified}>` keyed by `path`, only overwriting
  `modified` on repeat.

- [ ] **Step 4: Test**

  `ws-to-sse.test.ts`: a `file_diff` WS message maps to a valid `code.diff` StreamEvent
  (mirror the existing `file_generated` test case); malformed/missing fields map to `null`
  (mirror the file's existing defensive-parsing test cases). Component test: a `code.diff`
  event renders a `DiffViewer` with the right `original`/`modified`/`filename` props, and
  is collapsed by default.

  Run whatever command the existing tests in these two files/areas use (check
  `package.json` scripts — likely `npm test` or `npx vitest run <path>`).

---

### Task 3: Backend — skip full upstream context when a workspace is already bound

**Files:**
- Modify: `backend/agents_orchestrator/development_agent/development_agent_api.py`
  (`_build_dev_session_context` at line 116, its call site at line 436-440)
- Test: `backend/tests/test_development_agent_upstream_context.py` (existing file from the
  prior plan's Task 6 — add to it)

**Interfaces:**
- Consumes: `workspace_guidance`, already computed at the call site by
  `_bind_pulled_workspace` on line 437, one line before `_build_dev_session_context` is
  called — a non-empty string means a workspace is bound and ready. No new DB query needed;
  this is purely a threading change.
- Produces: `_build_dev_session_context` gains a `workspace_bound: bool = False` keyword
  arg. When `True`, it returns a short fixed guidance string instead of calling
  `build_context_for_project`/`build_context` at all.

**Steps:**

- [ ] **Step 1: Thread `workspace_bound` through**

  Change `_build_dev_session_context`'s signature to add `workspace_bound: bool = False`.
  At the top of the function body, before the existing `if project_id and tenant_id:`
  branch, add:
  ```python
  if workspace_bound:
      return (
          "[UPSTREAM CONTEXT — AVAILABLE ON DEMAND]\n"
          "This project already has a bound repository workspace, so the full "
          "Requirements/Design payload is not preloaded here — this keeps the session "
          "fast and scoped to the imported repo. Call read_design_artifact if a task "
          "genuinely needs the upstream requirements, HLD/LLD, or ADRs."
      )
  ```
  Leave the rest of the function (the `project_id`/`tenant_id` branch and the
  session-keyed fallback) unchanged — fresh/greenfield sessions with no bound workspace
  must keep getting full context exactly as today.

- [ ] **Step 2: Update the one call site (line 436-440)**

  `workspace_guidance` is already computed one line above this call
  (`workspace_guidance = await _bind_pulled_workspace(...)` at line 437). Pass
  `workspace_bound=bool(workspace_guidance)` into the `_build_dev_session_context` call
  right after it. Do not change the other call site at line ~604 (that one has no
  `workspace_guidance` in scope — leave it calling with the default `workspace_bound=False`
  unless you confirm it's the same first-message path with the same variable available;
  if you find it IS the same kind of call, thread it there too, but verify first rather than
  assuming).

- [ ] **Step 3: Test**

  Add a test asserting: when `workspace_bound=True`, `_build_dev_session_context` returns
  the short guidance string WITHOUT calling `build_context_for_project` (mock it and assert
  it's never called — mirror the mocking pattern already used in this test file for
  `build_context_for_project`). When `workspace_bound=False` (or omitted), existing
  behavior is unchanged — the existing tests in this file already cover that; just confirm
  they still pass, don't duplicate them.

  Run (from `backend/`):
  ```
  PYTHONPATH=. ./.venv/Scripts/python.exe -m pytest tests/test_development_agent_upstream_context.py -v
  ```

---

### Task 4: Backend — cancel orphaned turns on disconnect + per-session in-flight guard

**Files:**
- Modify: `backend/agents_orchestrator/development_agent/development_agent_api.py`
  (`websocket_endpoint` at line 297, `_process_ws_message` at line 374)
- Test: new `backend/tests/development/test_ws_inflight_guard.py`

**Context — why this is the fix, precisely:** `websocket_endpoint`'s loop (line 313-337)
directly `await`s `_process_ws_message(...)` inline. If the frontend's connection drops
mid-turn (its own idle/reconnect logic, or a network blip) and reconnects, `_process_ws_message`
for the ORIGINAL connection keeps running — nothing cancels it. Both `_process_ws_message`
calls (old, orphaned; new, from the reconnect) read/write the SAME `get_session(session_id)`
state object and drive the SAME LangGraph checkpoint (`thread_id: session_id`), because
session state is keyed by `session_id`, not by WebSocket connection. Whichever one finishes
last "wins" on whatever the frontend is currently listening to, which is what reads as a
duplicated/overlapping reply. This task removes the orphaned-task side of that race at its
source (cancel it), and adds a same-session concurrency guard as defense in depth in case a
cancellation ever loses the race with a new message arriving.

**Interfaces:**
- Produces: `websocket_endpoint` dispatches `_process_ws_message` as a tracked
  `asyncio.Task` instead of an inline `await`, and cancels it when the connection's
  `WebSocketDisconnect`/cleanup path fires. `_process_ws_message` (or a thin wrapper around
  its dispatch) checks a module-level per-session in-flight marker before starting and
  rejects a second concurrent turn for the same `session_id`.

**Steps:**

- [ ] **Step 1: Per-session in-flight guard**

  Add a module-level `_INFLIGHT_SESSIONS: set[str] = set()` near the top of
  `development_agent_api.py`. At the very start of `_process_ws_message` (line 374, before
  the `try:` block or as its first action inside), check: if `session_id in
  _INFLIGHT_SESSIONS`, send the client a clear WS message (e.g. `{"type": "stream_chunk",
  "content": "\n\n> ⏳ Still processing your previous message — please wait for it to
  finish before sending another.", "session_id": session_id}` followed by the existing
  `stream_end` shape) and return early WITHOUT touching agent state. Otherwise, add
  `session_id` to the set, and wrap the rest of the function's body so the id is always
  removed on exit — success, exception, or cancellation (a `try/finally` around the
  existing body is the natural shape here; do not let any exit path leave a session
  permanently marked in-flight).

- [ ] **Step 2: Track and cancel the task on disconnect**

  In `websocket_endpoint`, change the dispatch for `msg_type == "user_message_with_files"`
  from a direct `await` to a tracked task: create it with `asyncio.create_task(...)`, keep
  a reference (a per-connection `set` of tasks is enough — a single connection can only
  have one in-flight turn at a time given Step 1's guard, but track defensively in case
  that changes). In the `except WebSocketDisconnect:` branch (line 338-339) and the
  `except Exception:` branch (line 347-349), before/alongside the existing
  `manager.disconnect(websocket)` call, cancel every tracked task for this connection
  (`task.cancel()`) — do not await their completion with a timeout that could hang the
  handler; a fire-and-forget cancel is sufficient, since Step 1's `finally` already
  guarantees `_INFLIGHT_SESSIONS` cleanup even on cancellation (verify this: `asyncio.CancelledError`
  propagates through a `try/finally` correctly in Python — confirm the finally block still
  runs by testing it directly, don't assume).
  Re-check: does converting to `create_task` change the `while True: data =
  await websocket.receive_text()` loop's behavior in a way that breaks anything else in
  this handler (e.g. `clear_agents`/`session_cleanup` messages arriving while a turn is
  in-flight)? If so, note it in your report — that's a real design question, not a task
  detail, and the controller needs to know before marking this task reviewed.

- [ ] **Step 3: Test**

  Cover: (a) a second `user_message_with_files` for a session already in
  `_INFLIGHT_SESSIONS` gets rejected with the wait message and does NOT invoke the agent
  graph (mock/spy on whatever function actually drives the graph turn and assert it's not
  called a second time); (b) `_INFLIGHT_SESSIONS` is cleared after a normal completion; (c)
  `_INFLIGHT_SESSIONS` is cleared after a simulated exception during processing (assert via
  directly calling `_process_ws_message` with a mocked dependency that raises, then
  checking the set no longer contains the session id). Task-cancellation-on-disconnect
  (Step 2) is inherently harder to unit test cleanly — a direct test of the
  `websocket_endpoint` function's disconnect branch calling `task.cancel()` on tracked
  tasks is sufficient; a full live-socket-drop integration test is not required here (that
  live confirmation happens in Task 5).

  Run (from `backend/`):
  ```
  PYTHONPATH=. ./.venv/Scripts/python.exe -m pytest tests/development/test_ws_inflight_guard.py -v
  ```

---

### Task 5: Live verification + clean restart + decisions log

No new code — this task confirms Tasks 1-4 work together against the real app, the same
standard every other fix this session has been held to.

**Files:** `desicions and issues.txt` (repo root, append only, follow the exact established
format — read Issues 1-6 first for tone/structure before writing Issue 7+).

- [ ] **Step 1: Restart both services, self-verified**

  Kill and restart the backend (port 8004) and frontend (port 3000). For each, confirm
  readiness by making a real request (`curl`) and finding that exact request in the
  service's own tailed log output — not just "the process started." This is the standard
  established earlier this session after the ghost-port incidents; do not skip it.

- [ ] **Step 2: Confirm the diff cards live**

  Ask the user (or, if you can drive a browser tool, do it yourself) to send the Development
  Agent a message that edits an existing file in an already-pulled repo. Confirm: a
  collapsed diff card appears in the chat inline, expands to show a real Monaco diff on
  click, and the model's own text stays a short summary (no pasted code blocks in the
  prose).

- [ ] **Step 3: Confirm context trimming**

  For a project with an already-bound workspace, confirm (via a log line, or by having the
  agent report what upstream context it received if asked) that the full
  Requirements/Design payload is NOT injected on the first message — only the short
  on-demand guidance string from Task 3.

- [ ] **Step 4: Confirm the race fix**

  Reproduce the original failure mode as closely as possible: trigger a turn likely to run
  long (a non-trivial multi-file task), and watch whether a second/duplicate reply ever
  appears. If you can watch the browser's Network/WS tab, confirm no second WebSocket
  connection opens for the same session while the first turn is still active server-side —
  if Task 4's guard is working, this cannot happen. If it's still possible to trigger a
  visible duplicate despite Task 4's fix, that's a real finding — write it up plainly rather
  than declaring success prematurely; this whole task's job is confirming the fix actually
  lands, not confirming the code compiles.

- [ ] **Step 5: Write Issue 7 in `desicions and issues.txt`**

  Follow the exact format of Issues 1-6: what the user reported, root cause with file:line
  evidence, the fix, what was verified live and how, and anything found-but-deferred (e.g.
  if Step 4's live repro surfaces a residual edge case, or if the second
  `_build_dev_session_context` call site from Task 3 Step 2 turned out to need its own fix
  and that was deferred instead of done). Do not mark this complete until the live checks
  in Steps 2-4 actually passed — this file is a record of what's true, not what was
  attempted.
