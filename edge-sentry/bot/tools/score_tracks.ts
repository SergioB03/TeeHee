import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { runSim } from "../lib/sim";

export default defineTool({
  description: prompt`
    Re-run the AIML (AI/ML) track threat scorer on current contacts.
    Returns threatScore, threatLabel (benign|watch|hostile), classProbs, and features.
    v1 is a weighted heuristic; the feature vector is the hook for a trained model later.
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
      note: "AIML v1 = heuristic head (speed,snr,jam,prox,alt,closingOnBlue); swap aiml_scorer.cpp later.",
    };
  },
});