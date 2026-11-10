import { defineEval } from "@grok/agent-sdk/evals";

export default defineEval({
  tags: ["routing"],
  description: "Threat questions refresh scores with score_tracks.",
  async test(t) {
    await t.send(
      "Refresh the threat scores and tell me which contact is most dangerous right now.",
    );
    t.succeeded();
    t.calledTool("score_tracks");
  },
});
