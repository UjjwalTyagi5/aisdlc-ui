import { z } from "zod";

import { Notification } from "@/lib/schemas/notification";

import { api } from "./client";

/**
 * The stored shape adds the addressing fields the server filters on. They are
 * parsed rather than stripped so a mismatch surfaces here instead of silently
 * showing an empty bell.
 */
export const StoredNotification = Notification.extend({
  identityId: z.string().nullable(),
  role: z.string().nullable(),
});
export type StoredNotification = z.infer<typeof StoredNotification>;

export const listNotifications = () =>
  api("/notifications", { schema: z.array(StoredNotification) });

export const markNotificationsRead = () =>
  api("/notifications", {
    method: "POST",
    schema: z.object({ marked: z.number().int().nonnegative() }),
  });
