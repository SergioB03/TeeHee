import { defineEval } from "@grok/agent-sdk/evals";

export default defineEval({
  tags: ["routing"],
  description: "A brief after advancing time uses run_tick and reads the map.",
  async test(t) {
    await t.send(
      "Reset the sandbox, advance four ticks, and brief me on the most dangerous red contact and why.",
    );
    t.succeeded();
    t.calledTool("run_tick");
    t.calledTool("map_status");
  },
});
