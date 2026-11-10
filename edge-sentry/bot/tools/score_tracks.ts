import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { runSim } from "../lib/sim";

export default defineTool({
  description: prompt`
    Re-run the AIML (AI/ML) track threat scorer on current contacts.
    Returns threatScore, threatLabel (benign|watch|hostile), classProbs, and features.
    threatScore comes from a trained model: the probability that the contact harms a blue
    asset within 5 ticks, from observable motion and emissions only. heuristicScore is the
    older hand-weighted score, reported alongside for comparison.
  `,
  inputSchema: z.object({}),
  effect: "read",
  async execute(_input, ctx) {
    const result = await runSim(ctx.workspaceDir, ["score"]);
    if (!result.ok) {
      return { ok: false, error: result.error ?? "score failed" };
    }
    return {
      ok: true,
      tick: result.world?.tick,
      tracks: result.world?.tracks,
      note: "threatScore = trained model on observed features and track history (see ml/RESULTS.md); heuristicScore = hand-weighted baseline.",
    };
  },
});