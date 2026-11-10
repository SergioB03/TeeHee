import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { runSim } from "../lib/sim";

export default defineTool({
  description: prompt`
    Advance the terrain EW/air simulation by N ticks.
    Movement, jam fields, abstract engage checks, and AIML track rescoring run each tick.
  `,
  inputSchema: z.object({
    n: z.number().int().min(1).max(20).default(1).describe("Number of ticks."),
  }),
  effect: "idempotent",
  async execute({ n }, ctx) {
    const result = await runSim(ctx.workspaceDir, ["tick", "--n", String(n)]);
    if (!result.ok) {
      return { ok: false, error: result.error ?? "tick failed" };
    }
    return {
      ok: true,
      tick: result.world?.tick,
      tracks: result.world?.tracks,
      events: result.world?.events,
      units: result.world?.units,
    };
  },
});