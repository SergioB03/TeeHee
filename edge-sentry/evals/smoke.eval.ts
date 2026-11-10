import { defineEval } from "@grok/agent-sdk/evals";

export default defineEval({
  tags: ["smoke"],
  description: "Ops brief uses map_status on the terrain sandbox.",
  async test(t) {
    await t.send(
      "Reset the sandbox and give me a short ops brief of the map and AIML threats.",
    );
    t.succeeded();
    t.calledTool("map_status");
  },
});