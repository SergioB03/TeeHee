import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { runSim } from "../lib/sim";

export default defineTool({
  description: prompt`
    Issue a high-level sim order to a unit: move, altitude, orbit, jam, launch, cease, or defend.
    Aircraft fly at an altitude level 0 (ground) to 5: altitude climbs or dives to tz, orbit
    holds a small box where the aircraft is, and move takes an optional tz. Fighters and
    missiles may launch at aircraft; bombers and drones only at ground targets.
    defend is for a blue HQ only: its point defense covers against one contact at a
    time (give the contact as target). A covered contact's hit is blocked and a covered
    jammer's field is suppressed. It only works on a contact whose track shows locked: true. Always give a one-line reason; it is written to the
    decision log with the threat picture you acted on.
    This is abstract wargame control only — not real weapon or jammer control.
  `,
  inputSchema: z.object({
    id: z.string().describe("Unit id, e.g. R-JET1 or B-S1."),
    action: z.enum(["move", "altitude", "orbit", "jam", "launch", "cease", "defend"]),
    tx: z.number().int().optional().describe("Target x for move."),
    ty: z.number().int().optional().describe("Target y for move."),
    tz: z.number().int().min(0).max(5).optional().describe("Altitude level 0 to 5 for altitude, or optionally for move and orbit."),
    target: z.string().optional().describe("Target unit id for launch or defend."),
    reason: z.string().max(300).optional().describe("One line on why, for the decision log."),
  }),
  effect: "write",
  async execute({ id, action, tx, ty, tz, target, reason }, ctx) {
    const args = ["order", "--id", id, "--action", action];
    if (tx !== undefined) args.push("--tx", String(tx));
    if (ty !== undefined) args.push("--ty", String(ty));
    if (tz !== undefined) args.push("--tz", String(tz));
    if (target) args.push("--target", target);
    if (reason) args.push("--reason", reason);
    const result = await runSim(ctx.workspaceDir, args);
    if (!result.ok) {
      return { ok: false, error: result.error ?? "order failed" };
    }
    return {
      ok: true,
      tick: result.world?.tick,
      units: result.world?.units,
      tracks: result.world?.tracks,
      events: result.world?.events,
    };
  },
});
