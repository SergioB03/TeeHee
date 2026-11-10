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
  hp: number;
  integrity: number;
  jamRadius: number;
  targetId: string;
  alive: boolean;
  order: {
    action: string;
    tx: number;
    ty: number;
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
  snr: number;
  threatScore: number;
  threatLabel: string;
  classProbs: {
    benign: number;
    watch: number;
    hostile: number;
  };
  features: number[];
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

export type SimResult = {
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
  const { stdout, stderr } = await execFileAsync(
    simBinary,
    ["--world", world, ...args],
    { maxBuffer: 2 * 1024 * 1024 },
  );
  const raw = String(stdout || stderr || "").trim();
  try {
    const parsed = JSON.parse(raw) as {
      ok?: boolean;
      command?: string;
      error?: string;
      world?: SimWorld;
    };
    return {
      ok: Boolean(parsed.ok),
      command: parsed.command,
      error: parsed.error,
      world: parsed.world,
      raw,
    };
  } catch {
    return { ok: false, error: "invalid sim JSON", raw };
  }
}