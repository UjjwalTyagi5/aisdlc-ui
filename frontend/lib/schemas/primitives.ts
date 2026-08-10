import { z } from "zod";

/** ISO-8601 UTC timestamp string. Parse to Date in the UI only when needed. */
export const Timestamp = z.string().datetime({ offset: true });
export type Timestamp = z.infer<typeof Timestamp>;

export const Cost = z.object({
  usd: z.number().nonnegative(),
  inputTokens: z.number().int().nonnegative(),
  outputTokens: z.number().int().nonnegative(),
});
export type Cost = z.infer<typeof Cost>;

export const Pagination = z.object({
  page: z.number().int().positive(),
  pageSize: z.number().int().positive().max(200),
  total: z.number().int().nonnegative(),
});
export type Pagination = z.infer<typeof Pagination>;

export function paginated<T extends z.ZodTypeAny>(item: T) {
  return z.object({
    items: z.array(item),
    pagination: Pagination,
  });
}

export const ApiError = z.object({
  code: z.string(),
  message: z.string(),
  requestId: z.string().optional(),
  details: z.record(z.unknown()).optional(),
});
export type ApiError = z.infer<typeof ApiError>;
