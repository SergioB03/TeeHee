import { defineEval } from "@grok/agent-sdk/evals";

export default defineEval({
  tags: ["routing"],
  description: "Unit orders go through issue_order.",
  async test(t) {
    await t.send(
      "Order the red jammer R-J1 to cease, then confirm its new state.",
    );
    t.succeeded();
    t.calledTool("issue_order");
  },
});
