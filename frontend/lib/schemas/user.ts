import { z } from "zod";

import { UserId } from "./ids";
import { Role } from "./enums";
import { Timestamp } from "./primitives";

export const User = z.object({
  id: UserId,
  name: z.string().min(1),
  email: z.string().email(),
  avatarUrl: z.string().url().optional(),
  role: Role,
  createdAt: Timestamp,
});
export type User = z.infer<typeof User>;

/** Minimal shape used for author/owner chips — avoids leaking admin fields. */
export const UserRef = z.object({
  id: UserId,
  name: z.string(),
  email: z.string().email().optional(),
  avatarUrl: z.string().url().optional(),
  initials: z.string(),
});
export type UserRef = z.infer<typeof UserRef>;
