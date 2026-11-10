import type { ResolveFn } from "@/types";

// Hexagon sends {"event": "work_order.closed", "event_id": "...", "wo_id": "WO-1001"}.
// One run per work order: a repeated or retried event for a work order that already
// has a run is dropped.
export default (async (ctx) => {
  const raw = ctx.event.payload as Record<string, unknown>;
  const payload = ((raw.data ?? raw) as Record<string, unknown>) ?? {};
  if (payload.event !== undefined && payload.event !== "work_order.closed") return null;
  const woId = String(payload.wo_id ?? "").trim().toUpperCase();
  if (!/^WO-\d{4}$/.test(woId)) return null;
  const existing = await ctx.lookupCorrelation(woId);
  if (existing.length > 0) return null;
  return [{ action: "create_run" as const, agentConfigId: "main-agent", correlationKey: woId, title: `Work order ${woId}` }];
}) satisfies ResolveFn;
