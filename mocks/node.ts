import { setupServer } from "msw/node";

import { handlers } from "./handlers";

/** Used by Vitest / Playwright unit and integration tests (Chunk 16). */
export const server = setupServer(...handlers);
