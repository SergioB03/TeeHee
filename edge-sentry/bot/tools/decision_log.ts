import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { runSim } from "../lib/sim";

export default defineTool({
  description: prompt`
    Read the decision log: the most recent orders, spawns and resets, who issued each
    (agent, console or cli), the stated reason, whether the sim accepted it, and the
    threat picture at that moment. Use it to review or explain past decisions.
  `,
  inputSchema: z.object({
    n: z.number().int().min(1).max(100).default(10).describe("How many recent entries."),
  }),
  effect: "read",
  async execute({ n }, ctx) {
    const result = await runSim(ctx.workspaceDir, ["decisions", "--n", String(n)]);
    if (!result.ok) {
      return { ok: false, error: result.error ?? "could not read the decision log" };
    }
    return { ok: true, decisions: result.decisions ?? [] };
  },
});
