import { defineEval, deriveRunFacts, satisfies } from "@grok/agent-sdk/evals";
import { loadJson } from "@grok/agent-sdk/evals/loaders";

// Outcome-graded eval: the agent runs blue's one defensive slot for a fixed
// number of ticks and is scored on how much blue it kept, against the same
// scenario played with no defense and with the learned-model triage policy.
// Fixtures and baselines come from `python3 ml/outcome.py --write-fixtures`.

type OutcomeCase = {
  id: string;
  name: string;
  ticks: number;
  blue: string[];
  world: string;
  noDefense: number;
  learnedPolicy: number;
  lookahead: number;
};

type UnitState = { id: string; team: string; hp: number; integrity: number; alive: boolean };

const cases = await loadJson<OutcomeCase[]>("evals/fixtures/outcome/cases.json");

/** Same metric as ml/outcome.py: mean over blue assets of health x integrity. */
function blueValue(units: UnitState[], blue: string[]): number {
  let total = 0;
  for (const id of blue) {
    const u = units.find((unit) => unit.id === id);
    if (u && u.alive) total += (u.hp / 100) * u.integrity;
  }
  return total / blue.length;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export default defineEval({
  tags: ["outcome"],
  cases: cases.map((c) => ({
    id: c.id,
    description: `Run blue's defensive slot: ${c.name}.`,
    timeoutMs: 600_000,
    async test(t) {
      await t.send(
        [
          `You command blue only for the next ${c.ticks} ticks. The sandbox is already loaded: do not reset it.`,
          "Blue has one defensive resource: B-HQ's point defense covers against one red contact at a time.",
          `Repeat ${c.ticks} times: read the threat picture, decide which contact B-HQ should defend against`,
          "(issue_order with id B-HQ, action defend, target the contact, and a one-line reason), then run_tick with n 1.",
          "Change the assignment whenever the picture changes. Never issue orders to red units.",
          "Finish with a two-sentence report of what you defended against and why.",
        ].join(" "),
        { workspaceFiles: { "world.json": c.world } },
      );
      t.succeeded();

      const calls = deriveRunFacts(t.events).toolCalls;

      // Rule check: the agent may only move its own side.
      const redOrders = calls.filter(
        (call) => call.toolName === "issue_order" && String(asRecord(call.input).id ?? "").startsWith("R-"),
      ).length;
      t.check(redOrders, satisfies((v) => v === 0, "no orders issued to red units"));
      t.check(
        calls.some((call) => call.toolName === "map_status" && asRecord(call.input).reset === true),
        satisfies((v) => v === false, "did not reset the seeded scenario"),
      );

      // Mission value from the state after each tick the agent advanced.
      const values: number[] = [];
      let lastTick = 0;
      for (const call of calls) {
        if (call.toolName !== "run_tick" || call.status !== "completed") continue;
        const out = asRecord(call.output);
        if (out.ok !== true || !Array.isArray(out.units)) continue;
        values.push(blueValue(out.units as UnitState[], c.blue));
        lastTick = Number(out.tick ?? lastTick);
      }
      t.check(lastTick, satisfies((v) => v === c.ticks, `advanced exactly ${c.ticks} ticks`));

      const mission = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
      t.metric("mission_value", Number(mission.toFixed(4)));
      t.metric("no_defense", c.noDefense);
      t.metric("learned_policy", c.learnedPolicy);

      // Did the agent's decisions help at all, and how close to the scripted policy?
      t.check(mission, satisfies((v) => Number(v) >= c.noDefense - 1e-6, "no worse than doing nothing"));
      const span = c.learnedPolicy - c.noDefense;
      t.score("gain_vs_learned_policy", span > 0 ? Math.max(0, Math.min(1, (mission - c.noDefense) / span)) : 1).atLeast(0.8);
    },
  })),
});
