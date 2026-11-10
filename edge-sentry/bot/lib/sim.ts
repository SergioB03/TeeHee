import { execFile } from "node:child_process";
import { access, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const here = path.dirname(fileURLToPath(import.meta.url));
export const projectRoot = path.resolve(here, "../..");
export const simBinary = path.join(projectRoot, "native/build/edge-sim");

export type SimUnit = {
  id: string;
  type: string;
  team: string;
  x: number;
  y: number;
  /** Altitude level, 0 = ground, up to the world's ceiling (5). */
  z: number;
  hp: number;
  integrity: number;
  jamRadius: number;
  targetId: string;
  alive: boolean;
  order: {
    action: string;
    tx: number;
    ty: number;
    tz: number;
    targetId: string;
  };
};

export type SimTrack = {
  id: string;
  sourceUnitId: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  z: number;
  vz: number;
  snr: number;
  /** False when the track is too weak for point defense to shoot at the contact. */
  locked: boolean;
  threatScore: number;
  threatLabel: string;
  /** Which scorer produced threatScore: the trained model (default) or the hand-set baseline. */
  scorer?: "learned" | "heuristic" | "sequence";
  watchAt?: number;
  hostileAt?: number;
  heuristicScore?: number;
  learnedScore?: number;
  classProbs: {
    benign: number;
    watch: number;
    hostile: number;
  };
  features: number[];
  /** Inputs to the learned model: observable kinematics, emissions and effects. */
  observed?: number[];
};

export type SimEvent = {
  tick: number;
  kind: string;
  detail: string;
};

export type SimWorld = {
  tick: number;
  width: number;
  height: number;
  units: SimUnit[];
  tracks: SimTrack[];
  events: SimEvent[];
  cover: number[];
};

/** One line of the decision log: an order, spawn or reset and the picture it was made on. */
export type SimDecision = {
  at: string;
  tick: number;
  actor: string;
  command: string;
  args: Record<string, string>;
  reason: string;
  ok: boolean;
  error: string;
  picture: Array<{
    id: string;
    label: string;
    score: number;
    scorer: string;
    inputs: Record<string, number>;
  }>;
};

export type SimResult = {
  decisions?: SimDecision[];
  ok: boolean;
  command?: string;
  error?: string;
  world?: SimWorld;
  raw: string;
};

async function ensureBinary(): Promise<void> {
  try {
    await access(simBinary);
  } catch {
    throw new Error(
      `edge-sim binary missing at ${simBinary}. Build with: npm run build:native`,
    );
  }
}

export function worldPathFor(workspaceDir: string): string {
  return path.join(workspaceDir, "world.json");
}

export async function runSim(
  workspaceDir: string,
  args: string[],
): Promise<SimResult> {
  await ensureBinary();
  await mkdir(workspaceDir, { recursive: true });
  const world = worldPathFor(workspaceDir);
  let stdout = "";
  let stderr = "";
  try {
    ({ stdout, stderr } = await execFileAsync(
      simBinary,
      // Everything issued through the agent's tools is logged as actor "agent".
      ["--world", world, "--actor", "agent", ...args],
      { maxBuffer: 2 * 1024 * 1024 },
    ));
  } catch (err) {
    // edge-sim exits non-zero for rejected commands but still prints
    // {"ok":false,"error":...}. execFile rejects on any non-zero exit, so
    // recover that output here instead of letting the tool throw.
    const failed = err as { stdout?: unknown; stderr?: unknown; message?: string };
    stdout = String(failed.stdout ?? "");
    stderr = String(failed.stderr ?? "");
    if (!stdout && !stderr) {
      return {
        ok: false,
        error: failed.message ?? "edge-sim failed to run",
        raw: "",
      };
    }
  }
  const raw = String(stdout || stderr || "").trim();
  try {
    const parsed = JSON.parse(raw) as {
      ok?: boolean;
      command?: string;
      error?: string;
      world?: SimWorld;
      decisions?: SimDecision[];
    };
    return {
      ok: Boolean(parsed.ok),
      command: parsed.command,
      error: parsed.error,
      world: parsed.world,
      decisions: parsed.decisions,
      raw,
    };
  } catch {
    return { ok: false, error: "invalid sim JSON", raw };
  }
}