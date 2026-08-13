/**
 * The honest answer for a surface FastAPI does not implement yet.
 *
 * These routes used to serve `lib/mock/*` fixtures. That was correct while the
 * frontend ran standalone ([[no-backend-static-frontend]]), and became a lie the
 * moment `.env.local` set `NEXT_PUBLIC_API_MOCKS=off`: the page rendered a
 * confident directory of people who do not exist, over a database holding none
 * of them. A fabricated answer is worse than no answer, because nobody can tell
 * it is fabricated.
 *
 * So: reads return EMPTY and writes return 501. An empty read lets the page
 * render its own empty state — which is the truth, and which the designer
 * already wrote — while a 501 on a write stops the UI reporting success for a
 * change that was never persisted anywhere.
 *
 * Each call site names the FastAPI endpoint that would replace it, so the
 * backlog is readable from the code rather than from a document beside it.
 */

/** A write with nowhere to land. `endpoint` is the FastAPI route still owed. */
export function notImplemented(endpoint: string): Response {
  return Response.json(
    {
      code: "not_implemented",
      message: `This isn't wired to the backend yet — FastAPI does not implement ${endpoint}.`,
    },
    { status: 501 },
  );
}

/**
 * A read with nothing behind it. Deliberately a 200 with an empty body rather
 * than a 501: the page's job here is to show "nothing yet", and an error state
 * would claim the feature is broken when it is merely empty.
 */
export function emptyList(): Response {
  return Response.json([]);
}
