import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { runSim } from "../lib/sim";

export default defineTool({
  description: prompt`
    Read the current terrain sandbox: units (sensors, jammers, jets, missiles),
    AIML track threat scores, jam pressure effects, and recent sim events.
    Call this before briefing ops or after orders/ticks.
  `,
  inputSchema: z.object({
    reset: z
      .boolean()
      .optional()
      .describe("If true, reload the default scenario before status."),
  }),
  effect: ({ reset }) => (reset ? "write" : "read"),
  async execute({ reset }, ctx) {
    if (reset) {
      const resetResult = await runSim(ctx.workspaceDir, ["reset"]);
      if (!resetResult.ok) {
        return { ok: false, error: resetResult.error ?? "reset failed" };
      }
    }
    const result = await runSim(ctx.workspaceDir, ["status"]);
    if (!result.ok) {
      return { ok: false, error: result.error ?? "status failed" };
    }
    return {
      ok: true,
      tick: result.world?.tick,
      size: { width: result.world?.width, height: result.world?.height },
      units: result.world?.units,
      tracks: result.world?.tracks,
      events: result.world?.events,
    };
  },
});