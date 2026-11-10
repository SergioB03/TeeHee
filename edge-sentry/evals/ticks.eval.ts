import { defineEval } from "@grok/agent-sdk/evals";

export default defineEval({
  tags: ["routing"],
  description: "Advancing time uses run_tick.",
  async test(t) {
    await t.send(
      "Advance the sandbox by three ticks and tell me what changed.",
    );
    t.succeeded();
    t.calledTool("run_tick");
  },
});
