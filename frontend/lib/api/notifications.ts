import { z } from "zod";

import { Notification } from "@/lib/schemas/notification";

import { api } from "./client";

/**
 * THE ADDRESSING FIELDS ARE NOT ON THE WIRE. `recipient_user_id` / `recipient_role`
 * are what the server filters on, not something a client is told — a listing that
 * carried them would hand the reader the audience of every row, which is the scope
 * leak the addressed-never-broadcast rule exists to prevent. They were briefly
 * required here (`identityId`, `role`), mirroring the mock store's record shape;
 * MSW returned them and FastAPI never did, so the bell failed schema validation
 * against the real backend on every row.
 *
 * Validate the response shape only. Extra keys are stripped by Zod, so the mock
 * store's richer records still parse.
 */
export const listNotifications = () =>
  api("/notifications", { schema: z.array(Notification) });

export const markNotificationsRead = () =>
  api("/notifications", {
    method: "POST",
    schema: z.object({ marked: z.number().int().nonnegative() }),
  });
