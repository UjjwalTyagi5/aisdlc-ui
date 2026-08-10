import { z } from "zod";

import { TenantId } from "./ids";
import { Timestamp } from "./primitives";

export const Tenant = z.object({
  id: TenantId,
  name: z.string().min(1).max(200),
  plan: z.enum(["trial", "pilot", "enterprise"]),
  region: z.enum(["us", "eu"]),
  createdAt: Timestamp,
});
export type Tenant = z.infer<typeof Tenant>;
