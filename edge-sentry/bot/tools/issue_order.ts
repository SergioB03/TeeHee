import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { runSim } from "../lib/sim";

export default defineTool({
  description: prompt`
    Issue a high-level sim order to a unit: move, jam, launch, or cease.
    This is abstract wargame control only — not real weapon or jammer control.
  `,
  inputSchema: z.object({
    id: z.string().describe("Unit id, e.g. R-JET1 or B-S1."),
    action: z.enum(["move", "jam", "launch", "cease"]),
    tx: z.number().int().optional().describe("Target x for move."),
    ty: z.number().int().optional().describe("Target y for move."),
    target: z.string().optional().describe("Target unit id for launch."),
  }),
  effect: "write",
  async execute({ id, action, tx, ty, target }, ctx) {
    const args = ["order", "--id", id, "--action", action];
    if (tx !== undefined) args.push("--tx", String(tx));
    if (ty !== undefined) args.push("--ty", String(ty));
    if (target) args.push("--target", target);
    const result = await runSim(ctx.workspaceDir, args);
    if (!result.ok) {
      return { ok: false, error: result.error ?? "order failed" };
    }
    return {
      ok: true,
      tick: result.world?.tick,
      units: result.world?.units,
      tracks: result.world?.tracks,
      events: result.world?.events,
    };
  },
});